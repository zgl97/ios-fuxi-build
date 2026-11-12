#include "ClientGate.hpp"
#include <cassert>
#include <string>
static fuxi::ClientMode parse(std::string text,const char *name="fuxi-metal-smoke",const char *args=nullptr) {
 size_t n=text.size();text.push_back(0);return fuxi::parseClients(text.data(),n,name,args);
}
int main() {
 using M=fuxi::ClientMode;
 assert(parse("fuxi-metal-smoke\n")==M::allow);
 assert(parse("fuxi-ca-smoke\n","fuxi-ca-smoke")==M::allow);
 for(const char *name:{"backboardd","SpringBoard"}) {
  assert(parse(std::string(name)+"\n",name)==M::deny);
  assert(parse(std::string("census ")+name+"\n",name)==M::deny);
 }
 assert(parse("fuxi-metal-smoke\n","backboardd")==M::deny);
 assert(fuxi::backboardMode("x=1\tfuxibbgpu=2\nquiet")==2);
 assert(fuxi::backboardMode("fuxibbgpu=1")==1);
 assert(parse("backboardd\n","backboardd","fuxibbgpu=2")==M::allow);
 assert(parse("census backboardd\n","backboardd","fuxibbgpu=2")==M::deny);
 assert(parse("backboardd\n","SpringBoard","fuxibbgpu=2")==M::deny);
 const char *armed="other=1 fuxigpu=1 fuxibbgpu=1 quiet";
 assert(parse("backboardd\n","backboardd",armed)==M::allow);
 assert(parse("fuxi-ca-smoke\n","backboardd",armed)==M::deny);
 assert(parse("census backboardd\n","backboardd",armed)==M::deny);
 assert(parse("backboardd\ncensus backboardd\n","backboardd",armed)==M::deny);
 for(const char *args:{"", "fuxibbgpu=1-extra", "xfuxibbgpu=1", "fuxibbgpu=3", "fuxibbgpu=02", "fuxibbgpu=2-extra", "fuxibbgpu=1 fuxibbgpu=2", "fuxibbgpu=2 fuxibbgpu=2", "fuxigpu=1",
     "fuxibbgpu=1 fuxibbgpu=0", "fuxibbgpu=0 fuxibbgpu=1", "fuxibbgpu=1 fuxibbgpu=1"})
  assert(parse("backboardd\n","backboardd",args)==M::deny);
 for(const char *config:{"SpringBoard\n","census SpringBoard\n"}){assert(parse(config,"SpringBoard",armed)==M::deny);assert(parse(config,"SpringBoard","fuxibbgpu=2")==M::deny);}
 // Cooling (HackiOS): mediaserverd's shader preload and SpringBoard's Siri orb
 // abort on any visible device, so neither an allow nor a census line admits them.
 for(const char *config:{"mediaserverd\n","census mediaserverd\n","fuxi-ca-smoke\nmediaserverd\n"})
  for(const char *args:{(const char *)nullptr,armed,"fuxibbgpu=2"})assert(parse(config,"mediaserverd",args)==M::deny);
 assert(fuxi::hiddenClient("SpringBoard") && fuxi::hiddenClient("mediaserverd"));
 assert(!fuxi::hiddenClient("SpringBoard","fuxispringboard=1") && fuxi::hiddenClient("mediaserverd","fuxispringboard=1"));
 assert(!fuxi::hiddenClient("mediaserverd2") && !fuxi::hiddenClient("backboardd") && !fuxi::hiddenClient("fuxi-ca-smoke"));
 assert(parse("fuxi-ca-smoke\nmediaserverd\n","fuxi-ca-smoke")==M::allow);
 assert(parse("# comment\n\tfuxi-metal-smoke \r\n")==M::allow);
 assert(parse("census backboardd\n","backboardd")==M::deny);
 assert(parse("backboardd\ncensus backboardd\n","backboardd")==M::deny);
 assert(parse("fuxi-metal-smoke\nbad name\n")==M::deny);
 assert(parse(std::string("fuxi-metal-smoke\n\0",18))==M::deny);
 assert(parse("fuxi-metal-smoke-extra\n")==M::deny);
 assert(parse("")==M::deny);
 assert(parse("fuxi-metal-smoke\n","../fuxi-metal-smoke")==M::deny);
 char path[]="/tmp/fuxi-client-policy-XXXXXX";int fd=mkstemp(path);assert(fd>=0);
 const char *data="fuxi-metal-smoke\n";assert(write(fd,data,strlen(data))==(ssize_t)strlen(data));close(fd);
 assert(fuxi::readClients(path,"fuxi-metal-smoke")== (geteuid()==0?M::allow:M::deny));
 chmod(path,0666);assert(fuxi::readClients(path,"fuxi-metal-smoke")==M::deny);
 chmod(path,0600);std::string link=std::string(path)+"-link";assert(!symlink(path,link.c_str()));
 assert(fuxi::readClients(link.c_str(),"fuxi-metal-smoke")==M::deny);unlink(link.c_str());unlink(path);
 assert(fuxi::readClients(path,"fuxi-metal-smoke")==M::deny);
 puts("PASS: exact clients, hidden SpringBoard/mediaserverd, census/conflicts, malformed/NUL, missing/untrusted/writable/symlink policy fail closed");
}
