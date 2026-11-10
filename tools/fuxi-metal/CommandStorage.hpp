/* Empty CPU storage can move between commands only after their encoding is over. */
#pragma once
#include <array>
#include <atomic>
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <mutex>
#include <vector>
namespace fuxi {
template<class T> class CommandStorage {
 std::mutex mutex;std::array<std::vector<T>,4> idle;std::atomic<size_t> highWater{256};
public:
 static constexpr size_t initial=256,budget=8u*1024*1024;
 std::vector<T> acquire(){
  std::vector<T> result;
  {std::lock_guard<std::mutex> lock(mutex);for(auto &v:idle)if(v.capacity()){result.swap(v);break;}}
  auto reserve=highWater.load();if(result.capacity()<reserve)result.reserve(reserve);return result;
 }
 void release(std::vector<T> &v){
  v.clear();std::lock_guard<std::mutex> lock(mutex);highWater=std::max(highWater.load(),std::min(v.capacity(),budget/sizeof(T)));size_t bytes=0;for(auto &old:idle)bytes+=old.capacity()*sizeof(T);
  if(v.capacity()>(budget-bytes)/sizeof(T))return;
  for(auto &old:idle)if(!old.capacity()){old.swap(v);return;}
 }
};
template<class Handle> class UploadStorage {
 std::mutex mutex;std::array<Handle,8> idle{};
public:
 static constexpr size_t blockBytes=64u*1024;
 template<class Allocate> Handle acquire(Allocate allocate){
  {std::lock_guard<std::mutex> lock(mutex);for(auto &h:idle)if(h){auto result=h;h={};return result;}}
  return allocate(blockBytes);
 }
 template<class Free> void release(Handle h,bool reusable,Free free){
  if(reusable){std::lock_guard<std::mutex> lock(mutex);for(auto &old:idle)if(!old){old=h;return;}}
  free(h);
 }
 template<class Free> void clear(Free free){std::lock_guard<std::mutex> lock(mutex);for(auto &h:idle)if(h){free(h);h={};}}
};
template<class Handle> class UploadRing {
 std::array<Handle,64> blocks{};size_t count=0,used=UploadStorage<Handle>::blockBytes;
public:
 struct Slice {Handle handle;size_t offset,length;};
 template<class Allocate,class Contents> Slice copy(const void *bytes,size_t n,UploadStorage<Handle> &pool,Allocate allocate,Contents contents){
  if(!n || n>4096)throw std::runtime_error("inline bytes bound");
  size_t offset=(used+255)&~size_t(255);
  if(!count || n>UploadStorage<Handle>::blockBytes-offset){
   if(count==blocks.size())throw std::runtime_error("command upload byte budget");
   auto h=pool.acquire(allocate);blocks[count++]=h;offset=0;
  }
  auto *dst=static_cast<uint8_t *>(contents(blocks[count-1]))+offset;
  memcpy(dst,bytes,n);memset(dst+n,0,((n+15)&~size_t(15))-n);used=offset+n;return {blocks[count-1],offset,n};
 }
 // metal2vulkan accepts inline bytes only at offset 0: start a new block when the ring would align past zero.
 template<class Allocate,class Contents> Slice copyOffsetZero(const void *bytes,size_t n,UploadStorage<Handle> &pool,Allocate allocate,Contents contents){
  if(count && ((used+255)&~size_t(255)))used=UploadStorage<Handle>::blockBytes;
  return copy(bytes,n,pool,allocate,contents);
 }
 template<class Free> void release(UploadStorage<Handle> &pool,bool reusable,Free free){for(size_t i=0;i<count;++i){pool.release(blocks[i],reusable,free);blocks[i]={};}count=0;used=UploadStorage<Handle>::blockBytes;}
};
}
