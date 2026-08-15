/*
 *  I263Vectors.cpp
 *  Decoding of the I.263 Vectors
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
long	corrTab1[4] = {0, 1, 0, 0};
long	corrTab2[16] = {0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 1, 1};

//**************************************************************
void I263Decoder::DecodeMacroblockVectors (Block *block_ptr, long x, long y)
{
	long	MV1_h, MV1_v, MV2_h, MV2_v, MV3_h, MV3_v, MBV_h, MBV_v, tmp;
	long	blk_row_offs;
	bool	isFirstRow = false;
	bool	isLastColumn = false;
	Block	*row_up_ptr;
	
	// offset one row back in the buffer
	blk_row_offs = 0 - (this -> MB_width * 6);
	
	// setting up the decision flags
	if (this -> ghdr_found == 1 || y == 0)
		isFirstRow = true;
	if (x == (this -> MB_width - 1))
		isLastColumn = true;
	
	if (this -> MB_type != MB_INTER4V)
	{
		// one vector per macroblock
		if (isFirstRow)
		{
			// we can skip the MEDIAN-decoding in this case because MV1 = MV2 = MV3
			if (x == 0) {
				MV2_h = 0;
				MV2_v = 0;
			}
			else {
				MV2_h = (block_ptr - 5) -> mv_h;
				MV2_v = (block_ptr - 5) -> mv_v;
			}
		}
		else
		{
			// setting up the candidate predictor MV1
			if (x == 0) {
				MV1_h = 0;
				MV1_v = 0;
			}
			else {
				MV1_h = (block_ptr - 5) -> mv_h;
				MV1_v = (block_ptr - 5) -> mv_v;
			}
			
			// setting up the candidate predictor MV2
			row_up_ptr = block_ptr + blk_row_offs;
			MV2_h = (row_up_ptr + 2) -> mv_h;
			MV2_v = (row_up_ptr + 2) -> mv_v;
			
			// setting up the candidate predictor MV3
			if (isLastColumn) {
				MV3_h = 0;
				MV3_v = 0;
			}
			else {
				MV3_h = (row_up_ptr + 8) -> mv_h;
				MV3_v = (row_up_ptr + 8) -> mv_v;
			}
			
			// finding the median value for the horizontal component
			if (MV2_h < MV1_h) {
				tmp = MV2_h;
				MV2_h = MV1_h;
				MV1_h = tmp;
			}
			if (MV3_h < MV2_h) {
				MV2_h = MV3_h;
				if (MV2_h <= MV1_h)
					MV2_h = MV1_h;
			}
			
			// finding the median value for the vertical component
			if (MV2_v < MV1_v) {
				tmp = MV2_v;
				MV2_v = MV1_v;
				MV1_v = tmp;
			}
			if (MV3_v < MV2_v) {
				MV2_v = MV3_v;
				if (MV2_v <= MV1_v)
					MV2_v = MV1_v;
			}
		}// else
		
		// adding predictors to the vector differences
		MBV_h = (this -> mvd1_h) + MV2_h;
		MBV_v = (this -> mvd1_v) + MV2_v;
			
		// limiting
		if (this -> umv_mode == 1)
		{
			// unrestricted motion vector (UMV) mode is on !
			// horizonzal component
			if (MV2_h > 32) {
				if (MBV_h > 63)
					MBV_h -= 64;
			}
			else {
				if (MV2_h < -31) {
					if (MBV_h < -63)
						MBV_h += 64;
				}
			}
			// vertical component
			if (MV2_v > 32) {
				if (MBV_v > 63)
					MBV_v -= 64;
			}
			else {
				if (MV2_v < -31) {
					if (MBV_v < -63)
						MBV_v += 64;
				}
			}
		}//if (umv_mode == 1)
		else {// normal vectors
			if (MBV_h > 31)
				MBV_h -= 64;
			else {
				if (MBV_h < -32)
					MBV_h += 64;
			}
				
			if (MBV_v > 31)
				MBV_v -= 64;
			else {
				if (MBV_v < -32)
					MBV_v += 64;
			}
		}
			
		// store the decoded vector in the "Blocks"
		// luma blocks
		block_ptr -> mv_h = MBV_h;
		(block_ptr + 1) -> mv_h = MBV_h;
		(block_ptr + 2) -> mv_h = MBV_h;
		(block_ptr + 3) -> mv_h = MBV_h;
			
		block_ptr -> mv_v = MBV_v;
		(block_ptr + 1) -> mv_v = MBV_v;
		(block_ptr + 2) -> mv_v = MBV_v;
		(block_ptr + 3) -> mv_v = MBV_v;
			
		// chroma blocks
		MBV_h = (MBV_h >> 1) + corrTab1[MBV_h & 3];
		MBV_v = (MBV_v >> 1) + corrTab1[MBV_v & 3];
			
		(block_ptr + 4) -> mv_h = MBV_h;
		(block_ptr + 5) -> mv_h = MBV_h;
			
		(block_ptr + 4) -> mv_v = MBV_v;
		(block_ptr + 5) -> mv_v = MBV_v;
	}
	else {
		// four vectors per macroblock
		long	comp_offs_arr[12];
		long	mvd_h_arr[4];
		long	mvd_v_arr[4];
		long	blknum, mv_h_sum, mv_v_sum;
		Block	*wptr;
		
		// init the array of offsets to the components
		comp_offs_arr[0] = -5;				// MV1
		comp_offs_arr[1] = blk_row_offs + 2;// MV2
		comp_offs_arr[2] = blk_row_offs + 8;// MV3
		comp_offs_arr[3] = 0;				// MV1
		comp_offs_arr[4] = blk_row_offs + 3;// MV2
		comp_offs_arr[5] = blk_row_offs + 8;// MV3
		comp_offs_arr[6] = -3;				// MV1
		comp_offs_arr[7] = 0;				// MV2
		comp_offs_arr[8] = 1;				// MV3
		comp_offs_arr[9] = 2;				// MV1
		comp_offs_arr[10] = 0;				// MV2
		comp_offs_arr[11] = 1;				// MV3
		
		// copy the vector differences to the arrays
		mvd_h_arr[0] = this -> mvd1_h;
		mvd_h_arr[1] = this -> mvd2_h;
		mvd_h_arr[2] = this -> mvd3_h;
		mvd_h_arr[3] = this -> mvd4_h;
		
		mvd_v_arr[0] = this -> mvd1_v;
		mvd_v_arr[1] = this -> mvd2_v;
		mvd_v_arr[2] = this -> mvd3_v;
		mvd_v_arr[3] = this -> mvd4_v;
		
		mv_h_sum = 0;
		mv_v_sum = 0;
		
		// decoding four vectors
		for (blknum = 0; blknum < 4; blknum++)
		{
			// setting the MV1
			if ((blknum & 1) == 0 && x == 0) {
				MV1_h = 0;
				MV1_v = 0;
			}
			else {
				wptr = block_ptr + comp_offs_arr[blknum * 3];
				MV1_h = wptr -> mv_h;
				MV1_v = wptr -> mv_v;
			}
			
			// settig MV2 and MV3 if outside the picture at the top
			if (blknum < 2 && isFirstRow) {
				// MV2 = MV1
				MV2_h = MV1_h;
				MV2_v = MV1_v;
				
				if (isLastColumn) {
					MV3_h = 0;
					MV3_v = 0;
				}
				else {
					// MV3 = MV1
					MV3_h = MV1_h;
					MV3_v = MV1_v;
				}
			}
			else {
				// setting the MV2
				wptr = block_ptr + comp_offs_arr[blknum * 3 + 1];
				MV2_h = wptr -> mv_h;
				MV2_v = wptr -> mv_v;
				
				if (blknum < 2 && isLastColumn) {
					MV3_h = 0;
					MV3_v = 0;
				}
				else {
					wptr = block_ptr + comp_offs_arr[blknum * 3 + 2];
					MV3_h = wptr -> mv_h;
					MV3_v = wptr -> mv_v;
				}
			}
			
			// finding the median value for the horizontal component
			if (MV2_h < MV1_h) {
				tmp = MV2_h;
				MV2_h = MV1_h;
				MV1_h = tmp;
			}
			if (MV3_h < MV2_h) {
				MV2_h = MV3_h;
				if (MV2_h <= MV1_h)
					MV2_h = MV1_h;
			}
			
			// finding the median value for the vertical component
			if (MV2_v < MV1_v) {
				tmp = MV2_v;
				MV2_v = MV1_v;
				MV1_v = tmp;
			}
			if (MV3_v < MV2_v) {
				MV2_v = MV3_v;
				if (MV2_v <= MV1_v)
					MV2_v = MV1_v;
			}
			
			// adding predictors to the vector differences
			MBV_h = mvd_h_arr[blknum] + MV2_h;
			MBV_v = mvd_v_arr[blknum] + MV2_v;
			
			// limiting
			if (this -> umv_mode == 1)
			{
				// unrestricted motion vector (UMV) mode is on !
				// horizonzal component
				if (MV2_h > 32) {
					if (MBV_h > 63)
						MBV_h -= 64;
				}
				else {
					if (MV2_h < -31) {
						if (MBV_h < -63)
							MBV_h += 64;
					}
				}
				// vertical component
				if (MV2_v > 32) {
					if (MBV_v > 63)
						MBV_v -= 64;
				}
				else {
					if (MV2_v < -31) {
						if (MBV_v < -63)
							MBV_v += 64;
					}
				}
			}//if (umv_mode == 1)
			else {// normal vectors
				if (MBV_h > 31)
					MBV_h -= 64;
				else {
					if (MBV_h < -32)
						MBV_h += 64;
				}
				
				if (MBV_v > 31)
					MBV_v -= 64;
				else {
					if (MBV_v < -32)
						MBV_v += 64;
				}
			}
			
			// store the decoded vector
			(block_ptr + blknum) -> mv_h = MBV_h;
			(block_ptr + blknum) -> mv_v = MBV_v;
			
			// summation for the chroma vector
			mv_h_sum += MBV_h;
			mv_v_sum += MBV_v;
		}//for
		
		// calculate vectors for chroma
		MBV_h = (mv_h_sum >> 3) + corrTab2[mv_h_sum & 0xF];
		MBV_v = (mv_v_sum >> 3) + corrTab2[mv_v_sum & 0xF];
		
		// store the decoded vector
		(block_ptr + 4) -> mv_h = MBV_h;
		(block_ptr + 5) -> mv_h = MBV_h;
			
		(block_ptr + 4) -> mv_v = MBV_v;
		(block_ptr + 5) -> mv_v = MBV_v;		
	}//else
}// method

//***************************************************************
void I263Decoder::UMVLimitVector (long  blkNum, long edgeFlags, long *horVComp, long *vertVComp)
{
	long	maxValue = (blkNum < 4) ? 32 : 16;
	long	minValue = -maxValue;
	
	// if MB_x == 1
	if ((edgeFlags & 1) && (*horVComp < minValue))
		*horVComp = minValue;
	
	if ((edgeFlags & 2) && (*horVComp > maxValue))
		*horVComp = maxValue;
	
	if ((edgeFlags & 4) && (*vertVComp < minValue))
		*vertVComp = minValue;
	
	if ((edgeFlags & 8) && (*vertVComp > maxValue))
		*vertVComp = maxValue;
}

//***************************************************************
void I263Decoder::DecodeVectorsB (BMBlock *bmbPtr, Block *block_ptr, long *MB_MVF_h, long *MB_MVF_v,
										long *MB_MVB_h, long *MB_MVB_v)
{
	long	TRD, MVD_h, MVD_v, MVF_h, MVF_v, MVB_h, MVB_v;
	long	cnt, MVF_h_sum, MVF_v_sum, MVB_h_sum, MVB_v_sum;
	Block	*bptr;
	
	// temporal reference increment
	TRD = (this -> pic_tr) - (this -> old_tr);
	if (TRD <= 0)
		TRD += 256;
	
	if (bmbPtr -> pmb_type == MB_INTER4V)
	{
		// four vectors in this P-macroblock
		bptr = block_ptr;
		// zero the Sums
		MVF_h_sum = MVF_v_sum = MVB_h_sum = MVB_v_sum = 0;
		
		for (cnt = 0; cnt < 4; cnt++) {
			// decode the MVF first
			if ((this -> impr_pb_frames == 1) && (bmbPtr -> forward_pred == true)) {
				MVD_h = 0;
				MVD_v = 0;
			}
			else {
				MVD_h = ((bptr -> mv_h) * (this -> TRB)) / TRD;
				MVD_v = ((bptr -> mv_v) * (this -> TRB)) / TRD;
			}
			
			MVF_h = (bmbPtr -> mvdb_h) + MVD_h;
			MVF_v = (bmbPtr -> mvdb_v) + MVD_v;
			
			if (this -> umv_mode == 1)
			{
				// horizonzal component
				if (MVD_h > 32) {
					if (MVF_h > 63)
						MVF_h -= 64;
				}
				else {
					if (MVD_h < -31) {
						if (MVF_h < -63)
							MVF_h += 64;
					}
				}
				// vertical component
				if (MVD_v > 32) {
					if (MVF_v > 63)
						MVF_v -= 64;
				}
				else {
					if (MVD_v < -31) {
						if (MVF_v < -63)
							MVF_v += 64;
					}
				}
			}//if (umv_mode == 1)
			else {// normal vectors
				if (MVF_h > 31)
					MVF_h -= 64;
				else {
					if (MVF_h < -32)
						MVF_h += 64;
				}
				
				if (MVF_v > 31)
					MVF_v -= 64;
				else {
					if (MVF_v < -32)
						MVF_v += 64;
				}
			}//else
			
			MB_MVF_h[cnt] = MVF_h;
			MVF_h_sum += MVF_h;
			MB_MVF_v[cnt] = MVF_v;
			MVF_v_sum += MVF_v;
			
			// decode the MVB vector
			if (bmbPtr -> mvdb_h != 0)
				MVB_h = MVF_h - (bptr -> mv_h);
			else
				MVB_h = (((this -> TRB) - TRD) * bptr -> mv_h) / TRD;
			
			MB_MVB_h[cnt] = MVB_h;
			MVB_h_sum += MVB_h;
		
			if (bmbPtr -> mvdb_v != 0)
				MVB_v = MVF_v - (bptr -> mv_v);
			else
				MVB_v = (((this -> TRB) - TRD) * bptr -> mv_v) / TRD;
			
			MB_MVB_v[cnt] = MVB_v;
			MVB_v_sum += MVB_v;
			
			bptr++;
		}//for
		
		// calculate vectors for chroma
		MB_MVF_h[4] = MB_MVF_h[5] = (MVF_h_sum >> 3) + corrTab2[MVF_h_sum & 0xF];
		MB_MVF_v[4] = MB_MVF_v[5] = (MVF_v_sum >> 3) + corrTab2[MVF_v_sum & 0xF];
		MB_MVB_h[4] = MB_MVB_h[5] = (MVB_h_sum >> 3) + corrTab2[MVB_h_sum & 0xF];
		MB_MVB_v[4] = MB_MVB_v[5] = (MVB_v_sum >> 3) + corrTab2[MVB_v_sum & 0xF];
	}
	else {
		// one vector per MB
		// decode the MVF first
		if ((this -> impr_pb_frames == 1) && (bmbPtr -> forward_pred == true)) {
			MVD_h = 0;
			MVD_v = 0;
		}
		else {
			MVD_h = ((block_ptr -> mv_h) * (this -> TRB)) / TRD;
			MVD_v = ((block_ptr -> mv_v) * (this -> TRB)) / TRD;
		}
		
		MVF_h = (bmbPtr -> mvdb_h) + MVD_h;
		MVF_v = (bmbPtr -> mvdb_v) + MVD_v;
		
		if (this -> umv_mode == 1)
		{
			// unrestricted motion vector (UMV) mode is on !
			// horizonzal component
			if (MVD_h > 32) {
				if (MVF_h > 63)
					MVF_h -= 64;
			}
			else {
				if (MVD_h < -31) {
					if (MVF_h < -63)
						MVF_h += 64;
				}
			}
			// vertical component
			if (MVD_v > 32) {
				if (MVF_v > 63)
					MVF_v -= 64;
			}
			else {
				if (MVD_v < -31) {
					if (MVF_v < -63)
						MVF_v += 64;
				}
			}
		}//if (umv_mode == 1)
		else {// normal vectors
			if (MVF_h > 31)
				MVF_h -= 64;
			else {
				if (MVF_h < -32)
					MVF_h += 64;
			}
				
			if (MVF_v > 31)
				MVF_v -= 64;
			else {
				if (MVF_v < -32)
					MVF_v += 64;
			}
		}
		
		MB_MVF_h[0] =
		MB_MVF_h[1] =
		MB_MVF_h[2] =
		MB_MVF_h[3] = MVF_h;
		MB_MVF_h[4] = MB_MVF_h[5] = (MVF_h >> 1) + corrTab1[MVF_h & 3];
		
		MB_MVF_v[0] =
		MB_MVF_v[1] =
		MB_MVF_v[2] =
		MB_MVF_v[3] = MVF_v;
		MB_MVF_v[4] = MB_MVF_v[5] = (MVF_v >> 1) + corrTab1[MVF_v & 3];
		
		// decode the MVB vector
		if (bmbPtr -> mvdb_h != 0)
			MVB_h = MVF_h - (block_ptr -> mv_h);
		else
			MVB_h = (((this -> TRB) - TRD) * block_ptr -> mv_h) / TRD;
		
		if (bmbPtr -> mvdb_v != 0)
			MVB_v = MVF_v - (block_ptr -> mv_v);
		else
			MVB_v = (((this -> TRB) - TRD) * block_ptr -> mv_v) / TRD;
		
		MB_MVB_h[0] =
		MB_MVB_h[1] =
		MB_MVB_h[2] =
		MB_MVB_h[3] = MVB_h;
		MB_MVB_h[4] = MB_MVB_h[5] = (MVB_h >> 1) + corrTab1[MVB_h & 3];
		
		MB_MVB_v[0] =
		MB_MVB_v[1] =
		MB_MVB_v[2] =
		MB_MVB_v[3] = MVB_v;
		MB_MVB_v[4] = MB_MVB_v[5] = (MVB_v >> 1) + corrTab1[MVB_v & 3];
	}//else
}