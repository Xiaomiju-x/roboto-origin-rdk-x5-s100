// Factory depth coordinate conversion in the camera frame. No inferred RGB registration.
#include <OpenNI.h>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cmath>
using namespace openni;
int main(int argc,char**argv) {
    int seconds=argc==2?std::atoi(argv[1]):30;
    if(seconds<1||seconds>120) return 2;
    if(OpenNI::initialize()!=STATUS_OK) return 3;
    Device dev; VideoStream depth;
    if(dev.open(ANY_DEVICE)!=STATUS_OK) {OpenNI::shutdown(); return 4;}
    std::printf("{\"kind\":\"capabilities\",\"color_sensor\":%s,\"registration_supported\":%s,\"frame\":\"openni_depth_native\"}\n",
        dev.getSensorInfo(SENSOR_COLOR)?"true":"false",
        dev.isImageRegistrationModeSupported(IMAGE_REGISTRATION_DEPTH_TO_COLOR)?"true":"false");
    if(depth.create(dev,SENSOR_DEPTH)!=STATUS_OK||depth.start()!=STATUS_OK) {dev.close();OpenNI::shutdown();return 5;}
    int rc=0, count=0; auto start=std::chrono::steady_clock::now();
    while(std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count()<seconds) {
        VideoStream*ptr=&depth;int index=0;VideoFrameRef frame;
        if(OpenNI::waitForAnyStream(&ptr,1,&index,2000)!=STATUS_OK||depth.readFrame(&frame)!=STATUS_OK) {rc=6;break;}
        if(count++%15!=0) continue;
        const auto* data=(const DepthPixel*)frame.getData(); int w=frame.getWidth(),h=frame.getHeight(),stride=frame.getStrideInBytes()/sizeof(DepthPixel);
        auto ns=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
        std::printf("{\"kind\":\"cloud\",\"host_monotonic_ns\":%lld,\"width\":%d,\"height\":%d,\"hfov_rad\":%.6f,\"vfov_rad\":%.6f,\"points_m\":[",(long long)ns,w,h,depth.getHorizontalFieldOfView(),depth.getVerticalFieldOfView());
        bool first=true;
        for(int y=8;y<h;y+=16) for(int x=8;x<w;x+=16) {
            auto d=data[y*stride+x]; if(!d) continue;
            float wx=0,wy=0,wz=0;
            if(CoordinateConverter::convertDepthToWorld(depth,x,y,d,&wx,&wy,&wz)!=STATUS_OK) continue;
            if(!std::isfinite(wx)||!std::isfinite(wy)||!std::isfinite(wz)) continue;
            std::printf("%s[%.4f,%.4f,%.4f]",first?"":",",wx/1000,wy/1000,wz/1000);first=false;
        }
        std::puts("]}");std::fflush(stdout);
    }
    std::fprintf(stderr,"frames=%d exit=%d\n",count,rc);
    depth.stop();depth.destroy();dev.close();OpenNI::shutdown();return rc;
}
