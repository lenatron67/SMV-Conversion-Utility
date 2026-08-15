/*
 *  I263ReconstructFrame.cpp
 *	Functions needed to reconstruct I,P or B macroblocks
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
void (*proc_array[4]) (unsigned char *src, short *dst) = {		CopyBlock,
																HorizontalHalfpelInterpolation,
																VerticalHalfpelInterpolation,
																HorizAndVertHalfpelInterpolation};

//**************************************************************
void I263Decoder::ReconstructMacroblock (long MB_x, long MB_y, Block *block_ptr, short *coefsBuf, long edgeFlags)
{
	long	blockNum, procNum, outBufOffs, cnt;
	long	MV_h, MV_v;
	short	predBlk[64];
	short	*outBlock, *wptr;
	unsigned char	*refBlkPtr;
	long	remBlkOffsArr[4];
	
	// reconstruct all six blocks
	for (blockNum = 0; blockNum < 6; blockNum++, block_ptr++, coefsBuf += 64)
	{
		outBlock = coefsBuf;
		
		// select the output offset
		if (blockNum < 4)
			outBufOffs = this -> y_plane_offs;
		else if (blockNum == 4)
			outBufOffs = this -> u_plane_offs;
		else
			outBufOffs = this -> v_plane_offs;
				
		// reconstruct the INTER-Blocks
		if (block_ptr -> is_intra == false)
		{
			if (this -> adv_pred_mode == 1 && blockNum <= 3)
			{
				// ADVANCED PREDICTION MODE (only for luma blocks!)
				if (blockNum & 1) {
					remBlkOffsArr[0] = -1; // offset to the block at the left
					// offset to the block at the right
					remBlkOffsArr[1] = 5;
					if (MB_x == (this -> MB_width - 1))
						remBlkOffsArr[1] = 0;
				}
				else {
					remBlkOffsArr[1] = 1;
					remBlkOffsArr[0] = -5;
					if (MB_x == 0)
						remBlkOffsArr[0] = 0;
				}
				
				if (blockNum >= 2) {
					remBlkOffsArr[2] = -2; // offset to the block above
					remBlkOffsArr[3] = 0; // offset to the block below
				}
				else {
					remBlkOffsArr[3] = 2;
					if (MB_y == 0)
						remBlkOffsArr[2] = 0;
					else
						remBlkOffsArr[2] = 2 - (this -> MB_width * 6);
				}
				
				if (this -> pb_frames == 0) {
					// if one of the surrounding blocks was INTRA coded replace it by the current block
					for (cnt = 0; cnt < 4; cnt++) {
						if (remBlkOffsArr[cnt] != 0) {
							if ((block_ptr + (remBlkOffsArr[cnt] + blockNum)) -> is_intra)
								remBlkOffsArr[cnt] = 0;
						}
					}//for
				}//if
				
				// perform the overlapped motion compensation
				OverlappedMC (block_ptr, blockNum, edgeFlags, &(predBlk[0]), &(remBlkOffsArr[0]));			
			}
			else {
				// DEFAULT H.263 PREDICTION MODE
				MV_h = block_ptr -> mv_h;
				MV_v = block_ptr -> mv_v;
			
				// check/limit the MV
				if (this -> umv_mode == 1)
					UMVLimitVector (blockNum, edgeFlags, &MV_h, &MV_v);
			
				// DEBUG
				//this -> MvhSum += MV_h;
				//this -> MvvSum += MV_v;
				
				// select the corresponding interpolation routine
				procNum = ((MV_v & 1) << 1) | (MV_h & 1);
			
				// find the block in the reference frame pointed by the MV
				refBlkPtr = (this -> prev_frm_buf_ptr) + (block_ptr -> out_offset) + outBufOffs +
							((MV_v & -2) * (PITCH / 2)) + (MV_h >> 1);
			
				// copy/interpolate to form the prediciton
				(*proc_array[procNum]) (refBlkPtr, &(predBlk[0]));
			}
			
			// adding the error term to the prediction
			if (block_ptr -> tcoef_present == true)
				AddErrorTerm (&(predBlk[0]), coefsBuf);
				
			outBlock = &(predBlk[0]);
		}
		else {
			// clipping for INTRA-Blocks
			wptr = coefsBuf;
			
			for (cnt = 0; cnt < 64; cnt++)
				*wptr++ = this -> pclp[*wptr];
		}
			
		// output blocks
		OutputBlock (outBlock, (this -> curr_frm_buf_ptr) + outBufOffs + (block_ptr -> out_offset), PITCH);
	}//for
}

//***************************************************************
void I263Decoder::ReconstructMacroblockB (BMBlock *bmbPtr, Block *block_ptr, short *coefsBuf, long edgeFlags)
{
	long	blockNum, procNum, outBufOffs, cbpb;
	long	MVF_h, MVF_v;
	unsigned char	*refBlkPtr;
	short	predBlk[64];
	
	// arrays of vectors for all six blocks
	long	MB_MVF_h[6];
	long	MB_MVF_v[6];
	long	MB_MVB_h[6];
	long	MB_MVB_v[6];
	
	// decode forward and backward vectors
	DecodeVectorsB (bmbPtr, block_ptr, MB_MVF_h, MB_MVF_v, MB_MVB_h, MB_MVB_v);
	
	cbpb = bmbPtr -> cbpb;
	
	// reconstruct all six blocks
	for (blockNum = 0; blockNum < 6; blockNum++, cbpb += cbpb, block_ptr++)
	{
		MVF_h = MB_MVF_h[blockNum];
		MVF_v = MB_MVF_v[blockNum];
		
		// DEBUG
		//this -> MVF_h_sum += MB_MVF_h[blockNum];
		//this -> MVF_v_sum += MB_MVF_v[blockNum];
		//this -> MVB_h_sum += MB_MVB_h[blockNum];
		//this -> MVB_v_sum += MB_MVB_v[blockNum];
		
		// select the output offset
		if (blockNum < 4)
			outBufOffs = this -> y_plane_offs;
		else if (blockNum == 4)
			outBufOffs = this -> u_plane_offs;
		else
			outBufOffs = this -> v_plane_offs;
		
		// check/limit the MV
		if (this -> umv_mode == 1)
			UMVLimitVector (blockNum, edgeFlags, &(MVF_h), &(MVF_v));
		
		// select the corresponding interpolation routine
		procNum = ((MVF_v & 1) << 1) | (MVF_h & 1);
		
		// find the block in the reference frame pointed by the MVF
		refBlkPtr = (this -> prev_frm_buf_ptr) + (block_ptr -> out_offset) + outBufOffs +
					((MVF_v & -2) * (PITCH / 2)) + (MVF_h >> 1);
		
		// copy/interpolate to form a forward predicted block
		(*proc_array[procNum]) (refBlkPtr, &(predBlk[0]));
		
		// form the bidirectional prediction if selected
		if ((this -> impr_pb_frames == 0) || (bmbPtr -> forward_pred == false))
			BidirectionalPrediction (blockNum, MB_MVB_h[blockNum], MB_MVB_v[blockNum], (this -> curr_frm_buf_ptr) +
									(block_ptr -> out_offset) + outBufOffs, &(predBlk[0]));
		
		// add the error term if exists
		if (cbpb & 32)
			AddErrorTerm (&(predBlk[0]), coefsBuf);
		
		coefsBuf += 64;
		
		// output blocks
		OutputBlock (&(predBlk[0]), (this -> b_frm_buf_ptr) + outBufOffs + (block_ptr -> out_offset), PITCH);
	}//for
}

//***************************************************************
void I263Decoder::AddErrorTerm (short *dst, short *src)
{
	long	cnt;
	
	for (cnt = 8; cnt != 0; cnt--)
	{
		*dst = this -> pclp[*(src) + *(dst)];
		*(dst + 1) = this -> pclp[*(src + 1) + *(dst + 1)];
		*(dst + 2) = this -> pclp[*(src + 2) + *(dst + 2)];
		*(dst + 3) = this -> pclp[*(src + 3) + *(dst + 3)];
		
		*(dst + 4) = this -> pclp[*(src + 4) + *(dst + 4)];
		*(dst + 5) = this -> pclp[*(src + 5) + *(dst + 5)];
		*(dst + 6) = this -> pclp[*(src + 6) + *(dst + 6)];
		*(dst + 7) = this -> pclp[*(src + 7) + *(dst + 7)];
		
		src += 8;
		dst += 8;
	}
}

//***************************************************************
void I263Decoder::OverlappedMC (Block *block_ptr, long blockNum, long edgeFlags, short *outPred, long *surrBlocks)
{
	long	MV_h, MV_v, procNum, cnt;
	unsigned char	*refBlkPtr;
	Block	*bptr;
	short	*blockPtrs[4], *c, *a, *b, *l, *r;
	
	blockPtrs[0] = this -> advPredBuf + 64;
	blockPtrs[1] = this -> advPredBuf + 128;
	blockPtrs[2] = this -> advPredBuf + 192;
	blockPtrs[3] = this -> advPredBuf + 256;
	
	MV_h = block_ptr -> mv_h;
	MV_v = block_ptr -> mv_v;
	
	// check/limit the MV
	if (this -> umv_mode == 1)
		UMVLimitVector (blockNum, edgeFlags, &MV_h, &MV_v);
	
	// select the corresponding interpolation routine
	procNum = ((MV_v & 1) << 1) | (MV_h & 1);
			
	// find the block in the reference frame pointed by the MV
	refBlkPtr = (this -> prev_frm_buf_ptr) + (block_ptr -> out_offset) + (this -> y_plane_offs) +
				((MV_v & -2) * (PITCH / 2)) + (MV_h >> 1);
	
	// form a prediction for the current block in the buffer + 0
	(*proc_array[procNum]) (refBlkPtr, this -> advPredBuf);
	
	for (cnt = 0; cnt < 4; cnt++)
	{
		if (*surrBlocks) {
			bptr = block_ptr + *surrBlocks;
			MV_h = bptr -> mv_h;
			MV_v = bptr -> mv_v;
		
			// check/limit the MV
			if (this -> umv_mode == 1)
				UMVLimitVector (blockNum, edgeFlags, &MV_h, &MV_v);
		
			// select the corresponding interpolation routine
			procNum = ((MV_v & 1) << 1) | (MV_h & 1);
		
			// find the block in the reference frame pointed by the MV
			refBlkPtr = (this -> prev_frm_buf_ptr) + (block_ptr -> out_offset) + (this -> y_plane_offs) +
						((MV_v & -2) * (PITCH / 2)) + (MV_h >> 1);
	
			(*proc_array[procNum]) (refBlkPtr, blockPtrs[cnt]);
		}
		else
			blockPtrs[cnt] = this -> advPredBuf; // set to the current block
		
		surrBlocks++;
	}//for
	
	c = this -> advPredBuf;// current block data
	l = blockPtrs[0];// to the left of current block
	r = blockPtrs[1];// to the right of current block
	a = blockPtrs[2];// above of current block
	b = blockPtrs[3];// below of current block
	
	// line 1
	outPred[0] = (c[0] * 2 + a[0] + l[0] + 2) >> 2;
	outPred[1] = (c[1] * 5 + a[1] * 2 + l[1] + 4) >> 3;
	outPred[2] = (c[2] * 5 + a[2] * 2 + l[2] + 4) >> 3;
	outPred[3] = (c[3] * 5 + a[3] * 2 + l[3] + 4) >> 3;
	outPred[4] = (c[4] * 5 + a[4] * 2 + r[4] + 4) >> 3;
	outPred[5] = (c[5] * 5 + a[5] * 2 + r[5] + 4) >> 3;
	outPred[6] = (c[6] * 5 + a[6] * 2 + r[6] + 4) >> 3;
	outPred[7] = (c[7] * 2 + a[7] + r[7] + 2) >> 2;
	
	// line 2
	outPred[8] = (c[8] * 5 + a[8] + l[8] * 2 + 4) >> 3;
	outPred[9] = (c[9] * 5 + a[9] + l[9] * 2 + 4) >> 3;
	outPred[10] = (c[10] * 5 + a[10] * 2 + l[10] + 4) >> 3;
	outPred[11] = (c[11] * 5 + a[11] * 2 + l[11] + 4) >> 3;
	outPred[12] = (c[12] * 5 + a[12] * 2 + r[12] + 4) >> 3;
	outPred[13] = (c[13] * 5 + a[13] * 2 + r[13] + 4) >> 3;
	outPred[14] = (c[14] * 5 + a[14] + r[14] * 2 + 4) >> 3;
	outPred[15] = (c[15] * 5 + a[15] + r[15] * 2 + 4) >> 3;
	
	// line 3
	outPred[16] = (c[16] * 5 + a[16] + l[16] * 2 + 4) >> 3;
	outPred[17] = (c[17] * 5 + a[17] + l[17] * 2 + 4) >> 3;
	outPred[18] = (c[18] * 6 + a[18] + l[18] + 4) >> 3;
	outPred[19] = (c[19] * 6 + a[19] + l[19] + 4) >> 3;
	outPred[20] = (c[20] * 6 + a[20] + r[20] + 4) >> 3;
	outPred[21] = (c[21] * 6 + a[21] + r[21] + 4) >> 3;
	outPred[22] = (c[22] * 5 + a[22] + r[22] * 2 + 4) >> 3;
	outPred[23] = (c[23] * 5 + a[23] + r[23] * 2 + 4) >> 3;
	
	// line 4
	outPred[24] = (c[24] * 5 + a[24] + l[24] * 2 + 4) >> 3;
	outPred[25] = (c[25] * 5 + a[25] + l[25] * 2 + 4) >> 3;
	outPred[26] = (c[26] * 6 + a[26] + l[26] + 4) >> 3;
	outPred[27] = (c[27] * 6 + a[27] + l[27] + 4) >> 3;
	outPred[28] = (c[28] * 6 + a[28] + r[28] + 4) >> 3;
	outPred[29] = (c[29] * 6 + a[29] + r[29] + 4) >> 3;
	outPred[30] = (c[30] * 5 + a[30] + r[30] * 2 + 4) >> 3;
	outPred[31] = (c[31] * 5 + a[31] + r[31] * 2 + 4) >> 3;
	
	// line 5
	outPred[32] = (c[32] * 5 + b[32] + l[32] * 2 + 4) >> 3;
	outPred[33] = (c[33] * 5 + b[33] + l[33] * 2 + 4) >> 3;
	outPred[34] = (c[34] * 6 + b[34] + l[34] + 4) >> 3;
	outPred[35] = (c[35] * 6 + b[35] + l[35] + 4) >> 3;
	outPred[36] = (c[36] * 6 + b[36] + r[36] + 4) >> 3;
	outPred[37] = (c[37] * 6 + b[37] + r[37] + 4) >> 3;
	outPred[38] = (c[38] * 5 + b[38] + r[38] * 2 + 4) >> 3;
	outPred[39] = (c[39] * 5 + b[39] + r[39] * 2 + 4) >> 3;
	
	// line 6
	outPred[40] = (c[40] * 5 + b[40] + l[40] * 2 + 4) >> 3;
	outPred[41] = (c[41] * 5 + b[41] + l[41] * 2 + 4) >> 3;
	outPred[42] = (c[42] * 6 + b[42] + l[42] + 4) >> 3;
	outPred[43] = (c[43] * 6 + b[43] + l[43] + 4) >> 3;
	outPred[44] = (c[44] * 6 + b[44] + r[44] + 4) >> 3;
	outPred[45] = (c[45] * 6 + b[45] + r[45] + 4) >> 3;
	outPred[46] = (c[46] * 5 + b[46] + r[46] * 2 + 4) >> 3;
	outPred[47] = (c[47] * 5 + b[47] + r[47] * 2 + 4) >> 3;
	
	// line 7
	outPred[48] = (c[48] * 5 + b[48] + l[48] * 2 + 4) >> 3;
	outPred[49] = (c[49] * 5 + b[49] + l[49] * 2 + 4) >> 3;
	outPred[50] = (c[50] * 5 + b[50] * 2 + l[50] + 4) >> 3;
	outPred[51] = (c[51] * 5 + b[51] * 2 + l[51] + 4) >> 3;
	outPred[52] = (c[52] * 5 + b[52] * 2 + r[52] + 4) >> 3;
	outPred[53] = (c[53] * 5 + b[53] * 2 + r[53] + 4) >> 3;
	outPred[54] = (c[54] * 5 + b[54] + r[54] * 2 + 4) >> 3;
	outPred[55] = (c[55] * 5 + b[55] + r[55] * 2 + 4) >> 3;
	
	// line 8
	outPred[56] = (c[56] * 2 + b[56] + l[56] + 2) >> 2;
	outPred[57] = (c[57] * 5 + b[57] * 2 + l[57] + 4) >> 3;
	outPred[58] = (c[58] * 5 + b[58] * 2 + l[58] + 4) >> 3;
	outPred[59] = (c[59] * 5 + b[59] * 2 + l[59] + 4) >> 3;
	outPred[60] = (c[60] * 5 + b[60] * 2 + r[60] + 4) >> 3;
	outPred[61] = (c[61] * 5 + b[61] * 2 + r[61] + 4) >> 3;
	outPred[62] = (c[62] * 5 + b[62] * 2 + r[62] + 4) >> 3;
	outPred[63] = (c[63] * 2 + b[63] + r[63] + 2) >> 2;
}

//***************************************************************
void I263Decoder::BidirectionalPrediction (long blockNum, long MVB_h, long MVB_v, unsigned char *prec,
											short *fwPred)
{
	long	mh, mv, tmp, i_start, i_end, j_start, j_end, ypos, xpos;
	unsigned char *PrecPtr;
	
	if (blockNum <= 3) {
		mh = MVB_h + ((blockNum & 1) * 16); // mh+(nh*8)
		mv = MVB_v + ((blockNum & 2) * 8); // mv+(nv*8)
		
		// shortcut
		if (mh < -14 || mh > 30 || mv < -14)
			return;
		
		// i_start = MAX(0, (1 - mh)/2)
		tmp = (1 - mh) >> 1;
		i_start = ((tmp >> 31) ^ -1) & tmp;
		// i_end = MIN(7, (15 - mh + 1)/2)
		tmp = ((30 - mh) >> 1) - 7;
		i_end = ((tmp >> 31) & tmp) + 7;
		
		// j_start = MAX(0, (1 - mv)/2)
		tmp = (1 - mv) >> 1;
		j_start = ((tmp >> 31) ^ -1) & tmp;
		// j_end = MIN(7, (15 - mv + 1)/2)
		tmp = ((30 - mv) >> 1) - 7;
		j_end = ((tmp >> 31) & tmp) + 7;
	}
	else {
		// shortcut
		if (MVB_h < -14 || MVB_h > 14 || MVB_v < -14 || MVB_v > 14)
			return;
		
		// i_start = MAX(0, (1 - mhc)/2)
		tmp = (1 - MVB_h) >> 1;
		i_start = ((tmp >> 31) ^ -1) & tmp;
		// i_end = MIN(7, (7 - mhc + 1)/2)
		tmp = ((14 - MVB_h) >> 1) - 7;
		i_end = ((tmp >> 31) & tmp) + 7;
		
		// j_start = MAX(0, (1 - mvc)/2)
		tmp = (1 - MVB_v) >> 1;
		j_start = ((tmp >> 31) ^ -1) & tmp;
		// j_end = MIN(7, (7 - mvc + 1)/2)
		tmp = ((14 - MVB_v) >> 1) - 7;
		j_end = ((tmp >> 31) & tmp) + 7;
	}
	
	PrecPtr = prec + ((MVB_v & -2) * (PITCH / 2)) + (MVB_h >> 1);
	PrecPtr += j_start * PITCH;
	fwPred += j_start * 8;
	
	if (MVB_h & 1) {
		if (MVB_v & 1) {
			// horiz. and vert. half-pel MV
			for (ypos = j_start; ypos <= j_end; ypos++) {
				for (xpos = i_start; xpos <= i_end; xpos++)
					// bilinear interpolation & average
					fwPred[xpos] = (((PrecPtr[xpos] + PrecPtr[xpos + 1] + PrecPtr[xpos + PITCH] +
									PrecPtr[xpos + PITCH + 1] + 2) >> 2) + fwPred[xpos]) >> 1;
				
				PrecPtr += PITCH;
				fwPred += 8;
			}
		}//if (MVB_v & 1)
		else {
			// horiz. half-pel MV
			for (ypos = j_start; ypos <= j_end; ypos++) {
				for (xpos = i_start; xpos <= i_end; xpos++)
					// bilinear interpolation & average
					fwPred[xpos] = (((PrecPtr[xpos] + PrecPtr[xpos + 1] + 1) >> 1) + fwPred[xpos]) >> 1;
				
				PrecPtr += PITCH;
				fwPred += 8;
			}
		}
	}//if (MVB_h & 1)
	else {
		if (MVB_v & 1) {
			// vert. half-pel MV
			for (ypos = j_start; ypos <= j_end; ypos++) {
				for (xpos = i_start; xpos <= i_end; xpos++)
					// bilinear interpolation & average
					fwPred[xpos] = (((PrecPtr[xpos] + PrecPtr[xpos + PITCH] + 1) >> 1) + fwPred[xpos]) >> 1;
				
				PrecPtr += PITCH;
				fwPred += 8;
			}
		}//if (MVB_v & 1)
		else {
			// full-pel MV
			for (ypos = j_start; ypos <= j_end; ypos++) {
				for (xpos = i_start; xpos <= i_end; xpos++)
					fwPred[xpos] = (PrecPtr[xpos] + fwPred[xpos]) >> 1; // average
				
				PrecPtr += PITCH;
				fwPred += 8;
			}
		}
	}//else
}

//***************************************************************
void CopyBlock (unsigned char *src, short *dst)
{
	long	cnt;
	
	for (cnt = 8; cnt != 0; cnt--) {
		*dst = *src;
		*(dst + 1) = *(src + 1);
		*(dst + 2) = *(src + 2);
		*(dst + 3) = *(src + 3);
		*(dst + 4) = *(src + 4);
		*(dst + 5) = *(src + 5);
		*(dst + 6) = *(src + 6);
		*(dst + 7) = *(src + 7);
		
		src += PITCH;
		dst += 8;
	}
}

//***************************************************************
void HorizontalHalfpelInterpolation (unsigned char *src, short *dst)
{
	long	cnt;
	unsigned long	p1, p2, p3, p4;
	
	for (cnt = 8; cnt != 0; cnt--)
	{
		// grab four source pels
		p1 = *src;
		p2 = *(src + 1);
		p3 = *(src + 2);
		p4 = *(src + 3);
		
		// bilinear interpolation
		*dst = (p1 + p2 + 1) >> 1;
		*(dst + 1) = (p2 + p3 + 1) >> 1;
		*(dst + 2) = (p3 + p4 + 1) >> 1;
		p1 = *(src + 4);
		*(dst + 3) = (p4 + p1 + 1) >> 1;
		p2 = *(src + 5);
		*(dst + 4) = (p1 + p2 + 1) >> 1;
		p3 = *(src + 6);
		*(dst + 5) = (p2 + p3 + 1) >> 1;
		p4 = *(src + 7);
		*(dst + 6) = (p3 + p4 + 1) >> 1;
		p1 = *(src + 8);
		*(dst + 7) = (p4 + p1 + 1) >> 1;
	
		src += PITCH;
		dst += 8;
	}
}

//***************************************************************
void VerticalHalfpelInterpolation (unsigned char *src, short *dst)
{
	unsigned long	p1, p2, p3, p4;
	
	for (long cnt = 8; cnt != 0; cnt--)
	{
		p1 = *src;
		p2 = *(src + 384);
		p3 = *(src + 768);
		p4 = *(src + 1152);
		
		*dst = (p1 + p2 + 1) >> 1;
		*(dst + 8) = (p2 + p3 + 1) >> 1;
		*(dst + 16) = (p3 + p4 + 1) >> 1;
		p1 = *(src + 1536);
		*(dst + 24) = (p4 + p1 + 1) >> 1;
		p2 = *(src + 1920);
		*(dst + 32) = (p1 + p2 + 1) >> 1;
		p3 = *(src + 2304);
		*(dst + 40) = (p2 + p3 + 1) >> 1;
		p4 = *(src + 2688);
		*(dst + 48) = (p3 + p4 + 1) >> 1;
		p1 = *(src + 3072);
		*(dst + 56) = (p4 + p1 + 1) >> 1;
		
		src++;
		dst++;
	}
}

//***************************************************************
void HorizAndVertHalfpelInterpolation (unsigned char *src, short *dst)
{
	unsigned long	p1, p2, p3, p4, p5;
	
	for (long cnt = 8; cnt != 0; cnt--)
	{
		p1 = *src;
		p2 = *(src + 1);
		p3 = *(src + 384);
		p4 = *(src + 385);
		
		p4 = p4 + p2 + 1;
		*dst = ((p3 + p1 + 1) + p4) >> 2;
		
		p5 = *(src + 386) + *(src + 2) + 1;
		*(dst + 1) = (p5 + p4) >> 2;
		
		p4 = *(src + 387) + *(src + 3) + 1;
		*(dst + 2) = (p5 + p4) >> 2;
		
		p5 = *(src + 388) + *(src + 4) + 1;
		*(dst + 3) = (p5 + p4) >> 2;
		
		p4 = *(src + 389) + *(src + 5) + 1;
		*(dst + 4) = (p5 + p4) >> 2;
		
		p5 = *(src + 390) + *(src + 6) + 1;
		*(dst + 5) = (p5 + p4) >> 2;
		
		p4 = *(src + 391) + *(src + 7) + 1;
		*(dst + 6) = (p5 + p4) >> 2;
		
		p5 = *(src + 392) + *(src + 8) + 1;
		*(dst + 7) = (p5 + p4) >> 2;
		
		src += 384;
		dst += 8;
	}
}