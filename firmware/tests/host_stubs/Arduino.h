#pragma once
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <cstddef>
using boolean = bool;
using byte = uint8_t;
extern uint32_t clockMs;
inline unsigned long millis() { return clockMs; }
inline void yield() { ++clockMs; }
#define pgm_read_byte_near(p) (*(const unsigned char*)(p))
class Print {
 public:
  virtual ~Print() = default;
  virtual size_t write(uint8_t) = 0;
  virtual size_t write(const uint8_t* p, size_t n) { size_t done=0; while(done<n) done+=write(p[done]); return done; }
};
