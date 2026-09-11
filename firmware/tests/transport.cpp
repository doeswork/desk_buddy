#include <algorithm>
#include <cassert>
#include <deque>
#include <iostream>
#include <vector>
#include "PubSubClient.h"
#include "MqttStream.h"
#include "CameraFrameLease.h"
uint32_t clockMs = 0;
unsigned returnedFrames = 0;
void esp_camera_fb_return(camera_fb_t*) { ++returnedFrames; }
struct Socket : Client {
 bool live=false, zero=false, drop=false;
 size_t limit=10000000;
 uint32_t writeTime=0;
 std::vector<uint8_t> bytes;
 std::deque<uint8_t> input;
 int connect(const char*,uint16_t) override {live=true; input={0x20,2,0,0};return 1;}
 int connect(IPAddress,uint16_t p) override {return connect("",p);}
 uint8_t connected() override {return live;}
 void stop() override {live=false;}
 void flush() override {}
 int available() override {return input.size();}
 int read() override {int b=input.front();input.pop_front();return b;}
 size_t write(uint8_t b) override {return write(&b,1);}
 size_t write(const uint8_t* p,size_t n) override {
  clockMs+=writeTime;
  if(drop) {live=false;return 0;}
  if(zero) return 0;
  n=std::min(n,limit);bytes.insert(bytes.end(),p,p+n);return n;
 }
};
static bool send(MqttStream& stream, PubSubClient& client, const std::vector<uint8_t>& data) {
 bool ok=stream.write(client,data.data(),data.size(),[](){return clockMs;},[](unsigned ms){clockMs+=ms;});
 if(!ok) client.abortPublish();
 return ok;
}
int main() {
 Socket socket; PubSubClient client("localhost",1883,socket); client.setBufferSize(6144);
 auto connect=[&](){socket.limit=10000000;socket.zero=false;socket.drop=false;socket.writeTime=0;assert(client.connect("test"));socket.bytes.clear();};
 connect();
 for(unsigned remaining: {65534u,65535u,65536u,117071u,262144u,8388620u}) {
  assert(client.beginPublish("r/photos",remaining-10,false));
  unsigned value=0,multiplier=1;size_t i=1;
  do {value+=(socket.bytes[i]&127)*multiplier;multiplier*=128;} while(socket.bytes[i++]&128);
  assert(value==remaining);
  MqttStream stream(clockMs);std::vector<uint8_t> payload(remaining-10,0x42);
  assert(send(stream,client,payload)); assert(client.endPublish());
  assert(socket.bytes.size()==i+remaining); socket.bytes.clear();
 }
 assert(!client.beginPublish("r/photos",268435455u,false));assert(socket.bytes.empty());
 std::string oversized(6144,'x');assert(!client.beginPublish(oversized.c_str(),1,false));
 socket.limit=2;assert(!client.beginPublish("r/photos",20,false));assert(!socket.live);assert(socket.bytes.size()==2);
 connect(); assert(client.beginPublish("r/photos",100,false));socket.limit=3;
 MqttStream partial(clockMs);assert(send(partial,client,std::vector<uint8_t>(100,7)));
 client.abortPublish();connect();assert(client.beginPublish("r/photos",100,false));
 size_t header=socket.bytes.size();socket.zero=true;uint32_t start=clockMs;
 MqttStream stalled(clockMs);assert(!send(stalled,client,std::vector<uint8_t>(100,7)));
 assert(clockMs-start==2000);assert(!socket.live);assert(socket.bytes.size()==header);
 connect();assert(client.beginPublish("r/photos",100,false));socket.drop=true;
 MqttStream lost(clockMs);assert(!send(lost,client,std::vector<uint8_t>(100,7)));assert(!socket.live);
 connect();assert(client.beginPublish("r/photos",100,false));socket.limit=1;socket.writeTime=500;
 start=clockMs;MqttStream slow(clockMs);assert(!send(slow,client,std::vector<uint8_t>(100,7)));
 assert(clockMs-start>=15000 && clockMs-start<15500);assert(!socket.live);
 connect();clockMs=UINT32_MAX-100;assert(client.beginPublish("r/photos",100,false));socket.zero=true;
 start=clockMs;MqttStream wrapped(clockMs);assert(!send(wrapped,client,std::vector<uint8_t>(100,7)));assert(uint32_t(clockMs-start)==2000);
 connect();assert(client.beginPublish("r/photos",100,false));MqttStream recovered(clockMs);assert(send(recovered,client,std::vector<uint8_t>(100,7)));
 for(bool failure: {false,true}) {
  client.abortPublish();connect();
  auto capture=[&](){
   camera_fb_t frame;
   CameraFrameLease lease(&frame);
   assert(client.beginPublish("r/photos",100,false));
   socket.zero=failure;
   MqttStream stream(clockMs);
   return send(stream,client,std::vector<uint8_t>(100,7));
  };
  const unsigned before=returnedFrames;
  assert(capture()!=failure);
  assert(returnedFrames==before+1);
 }
 std::cout<<"OK: MQTT framing, bounds, partial writes, deadlines, disconnect and recovery\n";
}
