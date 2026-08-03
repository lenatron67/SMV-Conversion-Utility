/*
 *  I263BlockEdgeFilter.cpp
 *	I.263 Block edge filter
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
 
#include "I263Decoder.h"

//**************************************************************
void I263Decoder::InitBlockEdgeFilter ()
{
	long	cnt, QP, s, d, abs_d, sign_d, m1;
	
	for (cnt = 0; cnt <= 63; cnt++)
	{
		d = cnt - 32; // d = -32...+31
		// calculate ABS(d) without branching
		s = (d >> 31);
		abs_d = (d ^ s) - s;
		// SIGN(d)
		sign_d = (d < 0) ? -1 : 1;
		
		for (QP = 0; QP <= 31; QP++) {
			// MAX0(ABS(d) - (MAX0(ABS(d) * 2 - QP))) * SIGN(d)
			m1 = (abs_d * 2) - QP;
			if (m1 < 0)
				m1 = 0;
			
			m1 = abs_d - m1;
			if (m1 < 0)
				m1 = 0;
			
			this -> FilterTab[cnt][QP] = m1 * sign_d;
		}//for
	}
}

//**************************************************************
void I263Decoder::BlockEdgeFilter (unsigned char *y_ptr, unsigned char *u_ptr, unsigned char *v_ptr)
{
	// filtering the luma plane
	HorizontalFiltering (y_ptr, this -> internalWidth, this -> internalHeight, 4);
	VerticalFiltering (y_ptr, this -> internalWidth, this -> internalHeight, 4);
	
	// filtering the chroma planes
	HorizontalFiltering (u_ptr, (this -> internalWidth) >> 1, (this -> internalHeight) >> 1, 3);
	VerticalFiltering (u_ptr, (this -> internalWidth) >> 1, (this -> internalHeight) >> 1, 3);
	
	HorizontalFiltering (v_ptr, (this -> internalWidth) >> 1, (this -> internalHeight) >> 1, 3);
	VerticalFiltering (v_ptr, (this -> internalWidth) >> 1, (this -> internalHeight) >> 1, 3);
}

//**************************************************************
void I263Decoder::HorizontalFiltering (unsigned char *planePtr, long width, long height, long sizefactor)
{
	unsigned char *b1_ptr, *c1_ptr;
	long	A, B, C, D, d, d1, MB_y, MB_x, cnt, y, x, row_offset;
	
	b1_ptr = planePtr + PITCH * 7;
	c1_ptr = b1_ptr + PITCH;
	row_offset = PITCH * 8 - width;
	
	for (y = 8; y < height; y += 8)
	{
		MB_y = y >> sizefactor;
		
		for (x = 0; x < width; x += 8)
		{
			MB_x = x >> sizefactor;
			
			if (this -> MB_cod[MB_y][MB_x] || this -> MB_cod[MB_y + 1][MB_x]) {
				for (cnt = 8; cnt != 0; cnt--) {
					// d = (3A - 8B + 8C - 3D) / 16
					A = *(b1_ptr - PITCH) * 3;
					B = (*b1_ptr) * 8;
					C = (*c1_ptr) * 8;
					D = *(c1_ptr + PITCH) * 3;
					d = (A - B + C - D) >> 4;
	
					if (d && d >= -32 && d < 32) {
						d1 = this -> FilterTab[d + 32][this -> MB_quant[MB_y][MB_x]];
						*b1_ptr = this -> pclp[(*b1_ptr) + d1];
						*c1_ptr = this -> pclp[(*c1_ptr) - d1];
					}
				
					b1_ptr++;
					c1_ptr++;
				}
			}
			else {
				// skip this MB
				b1_ptr += 8;
				c1_ptr += 8;
			}
		}//for x
		
		b1_ptr += row_offset;
		c1_ptr += row_offset;
	}//for y
}

//**************************************************************
void I263Decoder::VerticalFiltering (unsigned char *planePtr, long width, long height, long sizefactor)
{
	unsigned char *wptr;
	long	A, B, C, D, d, d1, MB_y, MB_x, cnt, y, x;
	
	for (y = 0; y < height; y += 8)
	{
		MB_y = y >> sizefactor;
		
		for (x = 8; x < width; x += 8)
		{
			MB_x = x >> sizefactor;
			
			if (this -> MB_cod[MB_y + 1][MB_x - 1] || this -> MB_cod[MB_y + 1][MB_x]) {
				
				wptr = planePtr + x;
				
				for (cnt = 8; cnt != 0; cnt--) {
					// d = (3A - 8B + 8C - 3D) / 16
					A = *(wptr - 2) * 3;
					B = *(wptr - 1) * 8;
					C = *(wptr) * 8;
					D = *(wptr + 1) * 3;
					d = (A - B + C - D) >> 4;
	
					if (d && d >= -32 && d < 32) {
						d1 = this -> FilterTab[d + 32][this -> MB_quant[MB_y][MB_x]];
						*(wptr - 1) = this -> pclp[*(wptr - 1) + d1];
						*(wptr) = this -> pclp[*(wptr) - d1];
					}
				
					wptr += PITCH;
				}// for 8
			}//if
		}//for x
		
		planePtr += PITCH * 8;
	}//for y
}