// Astra Pro uses UVC color; follow Orbbec's astra_pro driver depth registration.
#include <OpenNI.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <cmath>
#include <csignal>
using namespace openni;
template<class T> void put(T v){std::fwrite(&v,sizeof(v),1,stdout);}
int main(int argc,char**argv){
 int seconds=argc==2?std::atoi(argv[1]):30;if(seconds<1||seconds>600)return 2;
 std::signal(SIGPIPE,SIG_IGN);
 if(OpenNI::initialize()!=STATUS_OK)return 3;
 Device dev;VideoStream depth;int rc=0,count=0;
 if(dev.open(ANY_DEVICE)!=STATUS_OK)return 4;
 auto original_registration=dev.getImageRegistrationMode();
 do {
  if(depth.create(dev,SENSOR_DEPTH)!=STATUS_OK){rc=5;break;}
  VideoMode dm=depth.getVideoMode();dm.setResolution(640,480);dm.setFps(30);dm.setPixelFormat(PIXEL_FORMAT_DEPTH_1_MM);
  if(depth.setVideoMode(dm)!=STATUS_OK){rc=6;break;}
  if(dev.setImageRegistrationMode(IMAGE_REGISTRATION_DEPTH_TO_COLOR)!=STATUS_OK){rc=7;break;}
  struct Params {float l[4],r[4],rot[9],trans[3],lk[5],rk[5];}params{};
  int size=sizeof(params);auto calibration_rc=dev.getProperty(14,&params,&size);
  bool valid=calibration_rc==STATUS_OK;
  for(int i=0;i<4;i++)valid=valid&&std::isfinite(params.r[i]);
  valid=valid&&params.r[0]>0&&params.r[1]>0;
  if(valid)std::fprintf(stderr,"{\"factory_calibration_rc\":%d,\"valid\":true,\"rgb_intrinsics_640\":[%.6f,%.6f,%.6f,%.6f]}\n",int(calibration_rc),params.r[0],params.r[1],params.r[2],params.r[3]);
  else {std::fprintf(stderr,"{\"factory_calibration_rc\":%d,\"valid\":false,\"rgb_intrinsics_640\":null}\n",int(calibration_rc));dev.setImageRegistrationMode(IMAGE_REGISTRATION_OFF);}
  depth.setMirroringEnabled(false);
  if(depth.start()!=STATUS_OK){rc=8;break;}
  auto start=std::chrono::steady_clock::now();
  while(std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<seconds){
   VideoFrameRef d;VideoStream*ptr=&depth;int index=0;
   if(OpenNI::waitForAnyStream(&ptr,1,&index,2000)!=STATUS_OK||depth.readFrame(&d)!=STATUS_OK){rc=9;break;}
   uint32_t w=d.getWidth(),h=d.getHeight();
   uint64_t td=d.getTimestamp(),tc=0;
   uint64_t mono=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
   std::fwrite("RGBD",4,1,stdout);put(w);put(h);put(td);put(tc);put(mono);put(uint32_t(dev.getImageRegistrationMode()));
   for(uint32_t y=0;y<h;y++)std::fwrite((const char*)d.getData()+y*d.getStrideInBytes(),w*2,1,stdout);
   if(std::fflush(stdout)==EOF)break;count++;
  }
 }while(false);
 std::fprintf(stderr,"RGBD_SUMMARY frames=%d registration=%d sync=%d exit=%d error=%s\n",count,int(dev.getImageRegistrationMode()),int(dev.getDepthColorSyncEnabled()),rc,OpenNI::getExtendedError());
 if(depth.isValid()){depth.stop();depth.destroy();}dev.setImageRegistrationMode(original_registration);dev.close();OpenNI::shutdown();return rc;
}
