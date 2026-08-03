/*
 *  BitStream.cpp
 *	Functions to parse the input bit stream
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

#include "BitStream.h"

extern unsigned long maskTab[33];

//************************* Get bits ****************************
unsigned long GetBits (BitStream *bs, long bits_needed)
{
	long	remain = bs -> remain_count, toRead;
	unsigned long	result = bs -> reservoir;
	unsigned char	*dptr = bs -> dataPtr;
	
	if (remain < bits_needed)
	{
		toRead = ((bits_needed + 7) - remain) >> 3;
		remain += toRead << 3;
		
		for (; toRead > 0; toRead--)
		{
			result = (result << 8) | *dptr;
			dptr++;
		}//for
	}//if
	
	bs -> dataPtr = dptr;
	remain -= bits_needed;
	bs -> remain_count = remain;
	bs -> reservoir = result & (maskTab[remain]);
	return (result >> remain);
}

//************************* Show bits ****************************
unsigned long ShowBits (BitStream *bs, long bits_needed)
{
	long	remain = bs -> remain_count, toRead;
	unsigned long	result = bs -> reservoir;
	unsigned char	*dptr = bs -> dataPtr;
	
	if (remain < bits_needed)
	{
		toRead = ((bits_needed + 7) - remain) >> 3;
		remain += toRead << 3;
		
		for (; toRead > 0; toRead--)
		{
			result = (result << 8) | *dptr;
			dptr++;
		}//for
	}//if
	
	bs -> dataPtr = dptr;
	bs -> remain_count = remain;
	bs -> reservoir = result;
	return (result >> (remain - bits_needed));
}

//************************* Skip bits ****************************
void SkipBits (BitStream *bs, long bskip)
{
	bs -> remain_count -= bskip;
	bs -> reservoir &= maskTab[bs -> remain_count];
}