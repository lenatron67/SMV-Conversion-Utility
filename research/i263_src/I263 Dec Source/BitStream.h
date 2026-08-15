/*
 *  BitStream.h
 *	The BitStream class definition
 *
 *  Free Implementation of the I.263 Video decoder
 *
 *	Created by Maxim Poliakovski <max_pole@gmx.de>
 *	
 *	This library is free software; you can redistribute it and/or
 *	modify it under the terms of the GNU Lesser General Public
 *	License as published by the Free Software Foundation; either
 *	version 2 of the License, or (at your option) any later version.
 */

#ifndef BIT_STREAM
#define BIT_STREAM

typedef struct BitStream
{
	unsigned char	*dataPtr;
	unsigned long	reservoir;
	long	remain_count;
} BitStream;

unsigned long GetBits (BitStream *bs, long bits_needed);
unsigned long ShowBits (BitStream *bs, long bits_needed);
void SkipBits (BitStream *bs, long bskip);

/* to mask the n least significant bits of an integer */
static unsigned long maskTab[33] = 
{	0x00000000,0x00000001,0x00000003,0x00000007,
	0x0000000F,0x0000001F,0x0000003F,0x0000007F,
	0x000000FF,0x000001FF,0x000003FF,0x000007FF,
	0x00000FFF,0x00001FFF,0x00003FFF,0x00007FFF,
	0x0000FFFF,0x0001FFFF,0x0003FFFF,0x0007FFFF,
	0x000FFFFF,0x001FFFFF,0x003FFFFF,0x007FFFFF,
	0x00FFFFFF,0x01FFFFFF,0x03FFFFFF,0x07FFFFFF,
	0x0FFFFFFF,0x1FFFFFFF,0x3FFFFFFF,0x7FFFFFFF,
	0xFFFFFFFF
};

#endif