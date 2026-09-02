#include <onnxruntime_cxx_api.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <sys/utsname.h>
#include <unistd.h>
#include <utility>
#include <vector>

namespace {

constexpr int kDefaultWarmupRuns = 1;
constexpr int kDefaultMeasuredRuns = 3;

int positive_run_count(const char* name, const int fallback) {
  const char* raw = std::getenv(name);
  if (raw == nullptr || std::string(raw).empty()) {
    return fallback;
  }
  std::size_t consumed = 0;
  const int value = std::stoi(raw, &consumed);
  if (consumed != std::string(raw).size() || value <= 0 || value > 1000000) {
    throw std::invalid_argument(std::string(name) + " must be in [1, 1000000]");
  }
  return value;
}

struct ModelSpec {
  std::string label;
  std::size_t expected_output_elements{};
  std::string path;
};

struct ModelResult {
  ModelSpec spec;
  bool passed{false};
  std::string error;
  std::vector<std::string> input_names;
  std::vector<std::vector<std::int64_t>> input_shapes;
  std::vector<std::string> output_names;
  std::vector<std::vector<std::int64_t>> output_shapes;
  std::size_t output_elements{0};
  std::size_t finite_elements{0};
  double output_min{0.0};
  double output_max{0.0};
  double output_mean{0.0};
  std::vector<double> output_values;
  std::vector<double> elapsed_ms;
};

std::string json_escape(const std::string& value) {
  std::ostringstream out;
  for (const unsigned char character : value) {
    switch (character) {
      case '"': out << "\\\""; break;
      case '\\': out << "\\\\"; break;
      case '\b': out << "\\b"; break;
      case '\f': out << "\\f"; break;
      case '\n': out << "\\n"; break;
      case '\r': out << "\\r"; break;
      case '\t': out << "\\t"; break;
      default:
        if (character < 0x20) {
          out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
              << static_cast<int>(character) << std::dec;
        } else {
          out << character;
        }
    }
  }
  return out.str();
}

std::string utc_timestamp() {
  const std::time_t now = std::time(nullptr);
  std::tm value{};
  gmtime_r(&now, &value);
  std::ostringstream out;
  out << std::put_time(&value, "%Y-%m-%dT%H:%M:%SZ");
  return out.str();
}

std::string hostname() {
  char buffer[256]{};
  if (gethostname(buffer, sizeof(buffer) - 1) != 0) {
    return "unknown";
  }
  return buffer;
}

std::string machine() {
  struct utsname info {};
  if (uname(&info) != 0) {
    return "unknown";
  }
  return info.machine;
}

std::size_t checked_element_count(const std::vector<std::int64_t>& shape) {
  if (shape.empty()) {
    return 1;
  }

  std::size_t result = 1;
  for (const std::int64_t dimension : shape) {
    if (dimension <= 0) {
      throw std::runtime_error("unresolved or invalid tensor dimension");
    }
    const auto size = static_cast<std::size_t>(dimension);
    if (result > std::numeric_limits<std::size_t>::max() / size) {
      throw std::overflow_error("tensor element count overflow");
    }
    result *= size;
  }
  return result;
}

std::vector<std::int64_t> resolve_input_shape(std::vector<std::int64_t> shape) {
  for (std::int64_t& dimension : shape) {
    if (dimension <= 0) {
      dimension = 1;
    }
  }
  checked_element_count(shape);
  return shape;
}

ModelSpec parse_spec(const std::string& argument) {
  const std::size_t first = argument.find('|');
  const std::size_t second = first == std::string::npos
      ? std::string::npos
      : argument.find('|', first + 1);
  if (first == std::string::npos || second == std::string::npos) {
    throw std::invalid_argument(
        "model argument must use label|expected_output_elements|absolute_path");
  }

  ModelSpec spec;
  spec.label = argument.substr(0, first);
  const std::string expected = argument.substr(first + 1, second - first - 1);
  spec.path = argument.substr(second + 1);
  if (spec.label.empty() || expected.empty() || spec.path.empty()) {
    throw std::invalid_argument("model argument contains an empty field");
  }

  std::size_t consumed = 0;
  spec.expected_output_elements = std::stoull(expected, &consumed);
  if (consumed != expected.size() || spec.expected_output_elements == 0) {
    throw std::invalid_argument("expected output element count must be positive");
  }
  return spec;
}

ModelResult probe_model(
    Ort::Env& environment,
    const ModelSpec& spec,
    const bool deterministic_input,
    const int warmup_runs,
    const int measured_runs) {
  ModelResult result;
  result.spec = spec;

  try {
    std::ifstream model_file(spec.path, std::ios::binary);
    if (!model_file.good()) {
      throw std::runtime_error("model file is not readable");
    }

    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(1);
    options.SetInterOpNumThreads(1);
    options.SetExecutionMode(ExecutionMode::ORT_SEQUENTIAL);
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_ENABLE_ALL);
    options.EnableCpuMemArena();
    options.EnableMemPattern();

    Ort::Session session(environment, spec.path.c_str(), options);
    const std::size_t input_count = session.GetInputCount();
    const std::size_t output_count = session.GetOutputCount();
    if (input_count != 1 || output_count != 1) {
      throw std::runtime_error(
          "expected exactly one input and one output, got " +
          std::to_string(input_count) + " input(s) and " +
          std::to_string(output_count) + " output(s)");
    }

    Ort::AllocatorWithDefaultOptions allocator;
    std::vector<std::vector<float>> input_buffers;
    std::vector<Ort::Value> input_tensors;
    input_buffers.reserve(input_count);
    input_tensors.reserve(input_count);
    result.input_names.reserve(input_count);
    result.input_shapes.reserve(input_count);

    for (std::size_t index = 0; index < input_count; ++index) {
      Ort::AllocatedStringPtr name = session.GetInputNameAllocated(index, allocator);
      result.input_names.emplace_back(name.get());
      const Ort::TypeInfo type_info = session.GetInputTypeInfo(index);
      const auto tensor_info = type_info.GetTensorTypeAndShapeInfo();
      if (tensor_info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
        throw std::runtime_error("only float32 model inputs are allowed");
      }
      result.input_shapes.push_back(resolve_input_shape(tensor_info.GetShape()));
      input_buffers.emplace_back(checked_element_count(result.input_shapes.back()), 0.0F);
      if (deterministic_input) {
        for (std::size_t item = 0; item < input_buffers.back().size(); ++item) {
          const double index_value = static_cast<double>(item);
          input_buffers.back()[item] = static_cast<float>(
              0.1 * std::sin(index_value * 0.013) +
              0.05 * std::cos(index_value * 0.007));
        }
      }
    }

    const Ort::MemoryInfo memory =
        Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);
    for (std::size_t index = 0; index < input_count; ++index) {
      input_tensors.emplace_back(Ort::Value::CreateTensor<float>(
          memory,
          input_buffers[index].data(),
          input_buffers[index].size(),
          result.input_shapes[index].data(),
          result.input_shapes[index].size()));
    }

    result.output_names.reserve(output_count);
    for (std::size_t index = 0; index < output_count; ++index) {
      Ort::AllocatedStringPtr name = session.GetOutputNameAllocated(index, allocator);
      result.output_names.emplace_back(name.get());
    }

    std::vector<const char*> input_name_ptrs;
    std::vector<const char*> output_name_ptrs;
    for (const std::string& name : result.input_names) {
      input_name_ptrs.push_back(name.c_str());
    }
    for (const std::string& name : result.output_names) {
      output_name_ptrs.push_back(name.c_str());
    }

    const auto run_once = [&]() {
      return session.Run(
          Ort::RunOptions{nullptr},
          input_name_ptrs.data(),
          input_tensors.data(),
          input_tensors.size(),
          output_name_ptrs.data(),
          output_name_ptrs.size());
    };

    for (int run = 0; run < warmup_runs; ++run) {
      auto warmup_outputs = run_once();
      if (warmup_outputs.size() != output_count) {
        throw std::runtime_error("warmup returned the wrong output count");
      }
    }

    std::vector<Ort::Value> outputs;
    result.elapsed_ms.reserve(measured_runs);
    for (int run = 0; run < measured_runs; ++run) {
      const auto start = std::chrono::steady_clock::now();
      outputs = run_once();
      const auto end = std::chrono::steady_clock::now();
      result.elapsed_ms.push_back(
          std::chrono::duration<double, std::milli>(end - start).count());

      if (outputs.size() != output_count) {
        throw std::runtime_error("measured run returned the wrong output count");
      }

      std::size_t run_elements = 0;
      std::size_t run_finite = 0;
      std::vector<std::vector<std::int64_t>> run_shapes;
      for (Ort::Value& output : outputs) {
        if (!output.IsTensor()) {
          throw std::runtime_error("model output is not a tensor");
        }
        const auto tensor_info = output.GetTensorTypeAndShapeInfo();
        if (tensor_info.GetElementType() != ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT) {
          throw std::runtime_error("only float32 model outputs are allowed");
        }
        const std::vector<std::int64_t> shape = tensor_info.GetShape();
        const std::size_t elements = tensor_info.GetElementCount();
        if (elements != checked_element_count(shape)) {
          throw std::runtime_error("runtime output shape and element count disagree");
        }
        const float* data = output.GetTensorData<float>();
        for (std::size_t item = 0; item < elements; ++item) {
          if (std::isfinite(data[item])) {
            ++run_finite;
          }
        }
        run_elements += elements;
        run_shapes.push_back(shape);
      }

      if (run_elements != spec.expected_output_elements) {
        throw std::runtime_error(
            "runtime output has " + std::to_string(run_elements) +
            " element(s), expected " +
            std::to_string(spec.expected_output_elements));
      }
      if (run_finite != run_elements) {
        throw std::runtime_error("runtime output contains NaN or infinity");
      }
      if (run == 0) {
        result.output_shapes = run_shapes;
      } else if (run_shapes != result.output_shapes) {
        throw std::runtime_error("runtime output shape changed between runs");
      }
      result.output_elements = run_elements;
      result.finite_elements = run_finite;
    }

    bool first_value = true;
    long double sum = 0.0;
    for (Ort::Value& output : outputs) {
      const auto tensor_info = output.GetTensorTypeAndShapeInfo();
      const std::size_t elements = tensor_info.GetElementCount();
      const float* data = output.GetTensorData<float>();
      for (std::size_t item = 0; item < elements; ++item) {
        const double value = static_cast<double>(data[item]);
        if (first_value) {
          result.output_min = value;
          result.output_max = value;
          first_value = false;
        } else {
          result.output_min = std::min(result.output_min, value);
          result.output_max = std::max(result.output_max, value);
        }
        sum += value;
        result.output_values.push_back(value);
      }
    }
    result.output_mean = static_cast<double>(sum / result.output_elements);
    result.passed = true;
  } catch (const std::exception& error) {
    result.error = error.what();
  }
  return result;
}

void write_shape(std::ostream& out, const std::vector<std::int64_t>& shape) {
  out << '[';
  for (std::size_t index = 0; index < shape.size(); ++index) {
    if (index != 0) out << ',';
    out << shape[index];
  }
  out << ']';
}

void write_named_shapes(
    std::ostream& out,
    const std::vector<std::string>& names,
    const std::vector<std::vector<std::int64_t>>& shapes) {
  out << '[';
  for (std::size_t index = 0; index < names.size(); ++index) {
    if (index != 0) out << ',';
    out << "{\"name\":\"" << json_escape(names[index]) << "\",\"shape\":";
    if (index < shapes.size()) {
      write_shape(out, shapes[index]);
    } else {
      out << "null";
    }
    out << ",\"dtype\":\"float32\"}";
  }
  out << ']';
}

void write_report(
    const std::vector<ModelResult>& results,
    bool passed,
    const bool deterministic_input,
    const int warmup_runs,
    const int measured_runs) {
  std::cout << std::setprecision(10);
  std::cout << "{\n"
            << "  \"schema_version\": 1,\n"
            << "  \"captured_at_utc\": \"" << utc_timestamp() << "\",\n"
            << "  \"hostname\": \"" << json_escape(hostname()) << "\",\n"
            << "  \"machine\": \"" << json_escape(machine()) << "\",\n"
            << "  \"onnxruntime_version\": \""
            << json_escape(OrtGetApiBase()->GetVersionString()) << "\",\n"
            << "  \"execution_provider\": \"CPU\",\n"
            << "  \"input_mode\": \""
            << (deterministic_input
                    ? "synthetic_deterministic_trigonometric_float32"
                    : "synthetic_zero_float32")
            << "\",\n"
            << "  \"device_access\": false,\n"
            << "  \"warmup_runs\": " << warmup_runs << ",\n"
            << "  \"measured_runs\": " << measured_runs << ",\n"
            << "  \"result\": \"" << (passed ? "PASS" : "FAIL") << "\",\n"
            << "  \"models\": [\n";

  for (std::size_t index = 0; index < results.size(); ++index) {
    const ModelResult& result = results[index];
    std::cout << "    {\"label\":\"" << json_escape(result.spec.label)
              << "\",\"path\":\"" << json_escape(result.spec.path)
              << "\",\"expected_output_elements\":"
              << result.spec.expected_output_elements
              << ",\"status\":\"" << (result.passed ? "PASS" : "FAIL") << "\"";
    if (!result.error.empty()) {
      std::cout << ",\"error\":\"" << json_escape(result.error) << "\"";
    }
    std::cout << ",\"inputs\":";
    write_named_shapes(std::cout, result.input_names, result.input_shapes);
    std::cout << ",\"outputs\":";
    write_named_shapes(std::cout, result.output_names, result.output_shapes);
    std::cout << ",\"output_elements\":" << result.output_elements
              << ",\"finite_elements\":" << result.finite_elements
              << ",\"output_stats\":{\"min\":" << result.output_min
              << ",\"max\":" << result.output_max
              << ",\"mean\":" << result.output_mean << "}"
              << ",\"output_values\":[";
    for (std::size_t item = 0; item < result.output_values.size(); ++item) {
      if (item != 0) std::cout << ',';
      std::cout << result.output_values[item];
    }
    std::cout << ']'
              << ",\"elapsed_ms\":[";
    for (std::size_t run = 0; run < result.elapsed_ms.size(); ++run) {
      if (run != 0) std::cout << ',';
      std::cout << result.elapsed_ms[run];
    }
    std::cout << "]}" << (index + 1 == results.size() ? "\n" : ",\n");
  }
  std::cout << "  ]\n}\n";
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    std::cerr << "Usage: " << argv[0]
              << " label|expected_output_elements|absolute_model_path [...]\n";
    return 2;
  }

  try {
    std::vector<ModelSpec> specs;
    for (int index = 1; index < argc; ++index) {
      specs.push_back(parse_spec(argv[index]));
    }

    const char* input_mode = std::getenv("ROBOTO_PROBE_INPUT_MODE");
    const bool deterministic_input =
        input_mode != nullptr && std::string(input_mode) == "deterministic";
    const int warmup_runs = positive_run_count(
        "ROBOTO_PROBE_WARMUP_RUNS", kDefaultWarmupRuns);
    const int measured_runs = positive_run_count(
        "ROBOTO_PROBE_MEASURED_RUNS", kDefaultMeasuredRuns);
    Ort::Env environment(ORT_LOGGING_LEVEL_WARNING, "roboto_offline_synthetic_probe");
    std::vector<ModelResult> results;
    results.reserve(specs.size());
    bool passed = true;
    for (const ModelSpec& spec : specs) {
      results.push_back(probe_model(
          environment, spec, deterministic_input, warmup_runs, measured_runs));
      passed = passed && results.back().passed;
    }
    write_report(results, passed, deterministic_input, warmup_runs, measured_runs);
    return passed ? 0 : 1;
  } catch (const std::exception& error) {
    std::cerr << "Probe setup failed: " << error.what() << '\n';
    return 2;
  }
}
