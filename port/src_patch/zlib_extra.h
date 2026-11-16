#pragma once
#include <zlib.h>
#ifdef __cplusplus
extern "C" {
#endif
int compress2(Bytef *dest, uLongf *destLen, const Bytef *source, uLong sourceLen, int level);
int uncompress(Bytef *dest, uLongf *destLen, const Bytef *source, uLong sourceLen);
uLong crc32(uLong crc, const Bytef *buf, uInt len);
#ifdef __cplusplus
}
#endif
