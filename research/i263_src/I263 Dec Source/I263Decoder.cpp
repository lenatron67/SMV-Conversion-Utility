/*
 *  I263Decoder.cpp
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

#include <string>
#include "I263Decoder.h"
#include "BitStream.h"
#include "I263BlockData.h"

I263Decoder::I263Decoder()
{
	this -> imageWidth = 0;
	this -> imageHeight = 0;
	this -> frame_size = 0;
	
	this -> old_tr = 0;
	this -> pic_tr = 0;
	this -> varC2 = 0;
	this -> pic_fmt = 0;
	
	this -> pclip = 0;
	this -> iclip = 0;
	this -> coefsBuf = 0;
	this -> blocks_buffer = 0;
	this -> yv12_buf0_ptr = 0;
	this -> yv12_buf1_ptr = 0;
	this -> curr_frm_buf_ptr = 0;
	this -> prev_frm_buf_ptr = 0;
	this -> b_frm_buf_ptr = 0;
	this -> bmb_buffer = 0;
	this -> advPredBuf = 0;
}

I263Decoder::~I263Decoder()
{
	//if (this -> FilterTabPtr)
	//	delete (this -> FilterTabPtr);
		
	if (this -> pclip)
		delete (this -> pclip);
		
	if (this -> iclip)
		delete (this -> iclip);
	
	if (this -> advPredBuf)
		delete (this -> advPredBuf);
	
	if (this -> bmb_buffer)
		delete (this -> bmb_buffer);
	
	if (this -> blocks_buffer)
		delete (this -> blocks_buffer);
	
	if (this -> coefsBuf)
		delete (this -> coefsBuf);
	
	if (this -> b_frm_buf_ptr)
		delete (this -> b_frm_buf_ptr);
	
	if (this -> yv12_buf1_ptr)
		delete (this -> yv12_buf1_ptr);
	
	if (this -> yv12_buf0_ptr)
		delete (this -> yv12_buf0_ptr);
}

long I263Decoder::Initialize (long width, long height)
{
	long	i, buf_size, ext_pict_size;
	long	x, y, blockNum;
	Block	*block_ptr;
	
	// absolut value
	if (width < 0)
		width = 0 - width;
	
	if (height < 0)
		height = 0 - height;
	
	// check the picture size
	if (width > MAX_WIDTH || width < 4 || height > MAX_HEIGHT || height < 4 || width & 3 || height & 3)
		return (-1);
		
	this -> imageWidth = width;
	this -> imageHeight = height;
	
	// internal buffers must be aligned to the macroblock boundary
	this -> internalWidth = (width + 15) & -16;
	this -> internalHeight = (height + 15) & -16;
	
	// extend the size of the picture for padding
	ext_pict_size = PITCH * (this -> internalHeight + 32);
	
	// allocate the YV12 output buffers
	buf_size = ext_pict_size + ext_pict_size / 2;
	this -> yv12_buf0_ptr = new unsigned char [buf_size];
	if (this -> yv12_buf0_ptr == 0)
		return (-1);
	
	this -> yv12_buf1_ptr = new unsigned char [buf_size];
	if (this -> yv12_buf1_ptr == 0)
		return (-1);
	
	this -> b_frm_buf_ptr = new unsigned char [buf_size];
	if (this -> b_frm_buf_ptr == 0)
		return (-1);
	
	memset (this -> yv12_buf0_ptr, 0, buf_size);
	memset (this -> yv12_buf1_ptr, 0, buf_size);
	memset (this -> b_frm_buf_ptr, 0, buf_size);
	
	// init the plane ptrs
	this -> y_plane_offs = 6160; // = PITCH * 16 + 16
	this -> v_plane_offs = ext_pict_size + 3080; // PITCH * 8 + 8
	this -> u_plane_offs = this -> v_plane_offs + (PITCH / 2);
	this -> curr_frm_buf_ptr = this -> yv12_buf0_ptr;
	this -> prev_frm_buf_ptr = this -> yv12_buf1_ptr;
	
	// calculate picture size in MBs
	this -> MB_width = (this -> internalWidth) >> 4;
	this -> MB_height = (this -> internalHeight) >> 4;
	
	// allocate a buffer to store the IDCT-coefficients
	this -> coefsBuf = new short[this -> MB_width * this -> MB_height * 12 * 64];
	if (this -> coefsBuf == 0)
		return (-1);
	
	// allocate the array of the "Blocks"
	this -> blocks_buffer = new Block[this -> MB_width * this -> MB_height * 6];
	if (this -> blocks_buffer == 0)
		return (-1);
	
	block_ptr = this -> blocks_buffer;
	
	// init the array of "Blocks"
	for (y = 0; y < (this -> MB_height); y++)
	{
		for (x = 0; x < (this -> MB_width); x++)
		{
			for (blockNum = 0; blockNum < 6; blockNum++)
			{
				block_ptr -> is_intra = false;
				block_ptr -> tcoef_present = false;
				
				// zero the motion vector
				block_ptr -> mv_h = 0;
				block_ptr -> mv_v = 0;
				// calculate the output offset
				if (blockNum < 4) {
					block_ptr -> out_offset = ((y * 16 * PITCH) + (x * 16)
							+ ((blockNum >> 1) * 8 * PITCH) + ((blockNum & 1) * 8));
				}
				else {
					block_ptr -> out_offset = (y * PITCH * 8) + (x * 8);
				}
				
				// calculate the number of this block
				block_ptr -> number = y * (this -> MB_width) + x * 6 + blockNum;
				block_ptr++;
			}
		}
	}
	
	// allocate the array of the BMBlock-structures
	this -> bmb_buffer = new BMBlock[this -> MB_width * this -> MB_height];
	if (this -> bmb_buffer == 0)
		return (-1);
	
	// allocate a buffer need for advanced prediction mode
	this -> advPredBuf = new short [320];
	if (this -> advPredBuf == 0)
		return (-1);
	
	// allocate the clipping table
	this -> iclip = new short [1024];
	if (this -> iclip == 0)
		return (-1);
	
	// initialize the clipping table
	this -> iclp = this -> iclip + 512;
	for (i= -512; i<512; i++)
		iclp[i] = (i<-256) ? -256 : ((i>255) ? 255 : i);
	
	// allocate the clipping table for pixels
	this -> pclip = new short [1024];
	if (this -> pclip == 0)
		return (-1);
	
	// initialize the clipping table
	this -> pclp = this -> pclip + 512;
	for (i= -512; i<512; i++)
		pclp[i] = (i < 0) ? 0 : ((i>255) ? 255 : i);
	
	// init the filter
	InitBlockEdgeFilter();
	
	return 0;
}

long I263Decoder::DecodeFrame (unsigned char *inputData, long framesize, unsigned char *outData, long row_offset)
{
	BitStream	bs;
	long	result, TRD, TRD1, newTR;;
	unsigned char	*tmpPtr;
	
	this -> frame_size = framesize;
	
	// init the BitStream object
	bs.dataPtr = inputData;
	bs.reservoir = 0;
	bs.remain_count = 0;
	
	if (framesize == 0 || framesize == 8) {
		if (this -> pb_frames == 1 && framesize != 0) {
			// temporal reference increment
			TRD = (this -> pic_tr) - (this -> old_tr);
			if (TRD < 0)
				TRD += 256;
			
			// new pic timestamp from the input stream
			newTR = *inputData;
			TRD1 = newTR - (this -> pic_tr);
			if (TRD1 < 0)
				TRD1 += 256;
			
			if (TRD1 != 0) {
				TRD -= this -> TRB;
				if (TRD > TRD1) {
					YUY2Output (this -> b_frm_buf_ptr, outData, row_offset);
					return (0);
				}
			}
		}
		
		YUY2Output (this -> prev_frm_buf_ptr, outData, row_offset);
		return (0);
	}
	
	result = DecodePictureHeader (&bs);
	if (result != 0)
		return (result);
	
	// reset the buffer sequence at each INTRA-picture
	if (this -> pic_type == INTRA) {
		this -> curr_frm_buf_ptr = this -> yv12_buf0_ptr;
		this -> prev_frm_buf_ptr = this -> yv12_buf1_ptr;
	}
	
	// padding edges
	if (this -> adv_pred_mode == 1 || this -> umv_mode == 1 || this -> DF_mode == 1)
	{
		EdgePaddingRoutine ((this -> y_plane_offs) + (this -> prev_frm_buf_ptr), true);
		EdgePaddingRoutine ((this -> u_plane_offs) + (this -> prev_frm_buf_ptr), false);
		EdgePaddingRoutine ((this -> v_plane_offs) + (this -> prev_frm_buf_ptr), false);
	}
	
	// DEBUG
	this -> coefSum = 0;
	this -> MvhSum = 0;
	this -> MvvSum = 0;
	this -> MVF_h_sum = 0;
	this -> MVF_v_sum = 0;
	this -> MVB_h_sum = 0;
	this -> MVB_v_sum = 0;
			
	result = DecodePicture (&bs);
	if (result != 0)
		return (result);
	
	// filtering
	if (this -> DF_mode)
	{
		BlockEdgeFilter ((this -> y_plane_offs) + (this -> curr_frm_buf_ptr),
							(this -> u_plane_offs) + (this -> curr_frm_buf_ptr),
							(this -> v_plane_offs) + (this -> curr_frm_buf_ptr));
		
		if (this -> pb_frames == 1)
			BlockEdgeFilter ((this -> y_plane_offs) + (this -> b_frm_buf_ptr),
							(this -> u_plane_offs) + (this -> b_frm_buf_ptr),
							(this -> v_plane_offs) + (this -> b_frm_buf_ptr));
	}
		
	if (this -> pb_frames == 1)
		YUY2Output (this -> b_frm_buf_ptr, outData, row_offset);
	else
		YUY2Output (this -> curr_frm_buf_ptr, outData, row_offset);
		
	// switch the buffers
	tmpPtr = this -> prev_frm_buf_ptr;
	this -> prev_frm_buf_ptr = this -> curr_frm_buf_ptr;
	this -> curr_frm_buf_ptr = tmpPtr;
		
	return 0;
}

//**************************************************************
long I263Decoder::DecodeMacroblock (BitStream *bs, long MB_x, long MB_y, Block *block_ptr, short *coefsBuf)
{
	long	cbp, quant, blockNum, result;
	bool	intradc_coded, tcoef_coded;
	
	// DEBUG
	long	coefCnt;
	
	// TODO: handle skipped MBs !
	if (this -> MB_coded == false) {
		this -> MB_type = MB_INTER;
		cbp = 0;
		
		this -> MB_cod[MB_y + 1][MB_x] = false;
		
		return (0);
	}
	else {
		// decode vectors
		if ((this -> pb_frames) || (this -> pic_type == INTER) && (this -> MB_type <= MB_INTER4V))
			DecodeMacroblockVectors (block_ptr, MB_x, MB_y);
	
		// merge cbpy and cbpc together
		cbp = ((this -> cbpy) << 2) | (this -> cbpc);
		
		// store the coded flag and the quant for the filtering later
		this -> MB_cod[MB_y + 1][MB_x] = true;
		this -> MB_quant[MB_y][MB_x] = this -> gquant;
	}
	
	quant = this -> gquant;
	
	// decode all six blocks
	for (blockNum = 0; blockNum < 6; blockNum++, cbp += cbp, block_ptr++)
	{
		if (cbp & 32) {
			intradc_coded = false;
			tcoef_coded = true;
			
			if (this -> MB_type >= MB_INTRA)
				intradc_coded = true;
		}
		else {
			intradc_coded = true;
			tcoef_coded = false;
			
			if (this -> MB_type < MB_INTRA)
				intradc_coded = false;
		}
		
		// init the info needed to reconstruct a MB
		block_ptr -> is_intra = intradc_coded;
		block_ptr -> tcoef_present = tcoef_coded;
			
		// decode IDCT coeffs
		if (intradc_coded || tcoef_coded) {
			result = DecodeBlock (bs, coefsBuf, intradc_coded, tcoef_coded, quant);
			if (result != 0)
				return (result);
			
			// DEBUG
			for (coefCnt = 0; coefCnt < 64; coefCnt++)
				this -> coefSum += coefsBuf[coefCnt];
			
			// perform the idct
			IDCT (coefsBuf);
		}
		
		coefsBuf += 64;
	}
	
	// decode B-Blocks
	if (this -> pb_frames == 1)
	{
		cbp = this -> cbpb;
		// calculate value for the BQUANT
		quant = ((this -> dbquant + 5) * (this -> pquant)) >> 2;
		// clip it in the range 1...31
		if (quant > 31)
			quant = 31;
		if (quant < 1)
			quant = 1;
		
		// decode all six blocks
		for (blockNum = 0; blockNum < 6; blockNum++, cbp += cbp)
		{
			if (cbp & 32) {
				result = DecodeBlock (bs, coefsBuf, false, true, quant);
				if (result != 0)
					return (result);
				
				// perform the idct
				IDCT (coefsBuf);
			}
			
			coefsBuf += 64;
		}//for
	}
	
	return (0);
}

//**************************************************************
long I263Decoder::DecodeBlock (BitStream *bs, short *coefs_buf, bool intradc_coded, bool tcoef_coded, long quant)
{
	long			intradc_flc, level, run, offset, sign, coef_num;
	unsigned long	vlc, sym;
	bool			last;
	
	coef_num = 0;
	
	if (intradc_coded)
	{
		// decode the INTRADC
		intradc_flc = GetBits (bs, 8);
		// my code !
		if (intradc_flc == 0 || intradc_flc == 0x80)
			return (-1);
		if (intradc_flc == 0xFF)
			intradc_flc = 0x80;
		
		*coefs_buf = intradc_flc << 3;
		coef_num++;
	}
	
	if (tcoef_coded == true)
	{	
		offset = quant << 5;
	
		// decode the TCOEFs
		for (last = false; (coef_num < 64) && (last == false); coef_num++)
		{
			vlc = ShowBits (bs, 13);
			sym = vlc_tab5[vlc >> 5];
			if (sym == 1) {
				// skip the ESCAPE-symbol
				SkipBits (bs, 7);
		
				last = GetBits (bs, 1);
				run = GetBits (bs, 6);
				level = GetBits (bs, 8);
		
				if (level == 0 || level == 0x80)
					return (-1);
				
				// level is a signed value, so extend it !!!
				if (level >= 128)
					level = level - 256;
		
				// inverse quantization
				level *= (this -> gquant);
				sign = level >> 31;
				level = level * 2 + (((((this -> gquant) - 1) | 1) ^ sign) - sign);
		
				// clipping
				level = (level < -2048) ? -2048 : (level > 2047) ? 2047 : level;
			}
			else {
				if ((sym & 1) && (sym >> 1))
					sym = vlc_tab6[vlc];
				else
					sym >>= 1;
		
				level = lev_tab[(sym & 0xFF) + offset];
				run = ((sym >> 8) & 0xFF) - 1;
				last = (sym >> 16) & 1;
				SkipBits (bs, (sym >> 17) & 0x1F);
			}
			
			// check run overflow
			if ((coef_num + run) > 63)
				return (-1);
			
			// de-rle
			if (run > 0) {
				for (; run > 0; run--, coef_num++)
					*(coefs_buf + zigzag_tab[coef_num]) = 0;
			}
		
			*(coefs_buf + zigzag_tab[coef_num]) = level;
		}// for
	}// if tcoef_coded
	
	// clear remain
	if (coef_num < 64) {
		for (; coef_num < 64; coef_num++)
			*(coefs_buf + zigzag_tab[coef_num]) = 0;
	}
	
	return (0);
}

//**************************************************************
long I263Decoder::DecodePicture (BitStream *bs)
{
	long	result, MB_x, MB_y, bcnt, edgeFlags;
	Block	*block_ptr = this -> blocks_buffer;
	short	*coefsPtr;
	BMBlock *bmbPtr;
	
	// clear the vectors
	for (bcnt = (this -> MB_width * this -> MB_height) * 6; bcnt != 0; bcnt--) {
		block_ptr -> is_intra = false;
		block_ptr -> tcoef_present = false;
		block_ptr -> mv_h = 0;
		block_ptr -> mv_v = 0;
		block_ptr++;
	}
	
	// setup the ptrs
	block_ptr = this -> blocks_buffer;
	coefsPtr = this -> coefsBuf;
	bmbPtr = this -> bmb_buffer;
	
	// recover coeffs and vectors from the input stream
	for (MB_y = 0; MB_y < (this -> MB_height); MB_y++)
	{
		if (DecodeGOBHeader (bs, MB_y) != 0)
			return (-1);
	
		for (MB_x = 0; MB_x < (this -> MB_width); MB_x++)
		{
			result = DecodeMacroblockHeader (bs, bmbPtr);
			if (result != 0)
				return (result);
		
			result = DecodeMacroblock (bs, MB_x, MB_y, block_ptr, coefsPtr);
			if (result != 0)
				return (result);
			
			block_ptr += 6;
			coefsPtr += 384;
			
			if (this -> pb_frames)
				coefsPtr += 384;
				
			bmbPtr++;
		}
	}
	
	// setup the ptrs
	block_ptr = this -> blocks_buffer;
	coefsPtr = this -> coefsBuf;
	bmbPtr = this -> bmb_buffer;
	
	// reconstruct the picture
	for (MB_y = 0; MB_y < (this -> MB_height); MB_y++)
	{
		for (MB_x = 0; MB_x < (this -> MB_width); MB_x++)
		{
			// determine if the current MB at the picture boundaries and set the flags
			if (this -> umv_mode == 1) {
				edgeFlags = 0;
		
				if (MB_x == 0)
					edgeFlags = 1;
		
				if (MB_x == (this -> MB_width - 1))
					edgeFlags |= 2;
		
				if (MB_y == 0)
					edgeFlags |= 4;
		
				if (MB_y == (this -> MB_height - 1))
					edgeFlags |= 8;
			}
			
			// reconstruct a I- or P-macroblock first
			ReconstructMacroblock (MB_x, MB_y, block_ptr, coefsPtr, edgeFlags);
			
			coefsPtr += 384;
			
			if (this -> pb_frames) {
				ReconstructMacroblockB (bmbPtr, block_ptr, coefsPtr, edgeFlags);
				coefsPtr += 384;
			}
			
			block_ptr += 6;
			bmbPtr++;
		}
	}
		
	return 0;
}

//***************************************************************
void I263Decoder::OutputBlock (short *block_p, unsigned char *outBuf, long row_offset)
{
	*outBuf = (unsigned char)(*block_p);
	*(outBuf + 1) = (unsigned char)*(block_p + 1);
	*(outBuf + 2) = (unsigned char)*(block_p + 2);
	*(outBuf + 3) = (unsigned char)*(block_p + 3);
	*(outBuf + 4) = (unsigned char)*(block_p + 4);
	*(outBuf + 5) = (unsigned char)*(block_p + 5);
	*(outBuf + 6) = (unsigned char)*(block_p + 6);
	*(outBuf + 7) = (unsigned char)*(block_p + 7);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 8);
	*(outBuf + 1) = (unsigned char)*(block_p + 9);
	*(outBuf + 2) = (unsigned char)*(block_p + 10);
	*(outBuf + 3) = (unsigned char)*(block_p + 11);
	*(outBuf + 4) = (unsigned char)*(block_p + 12);
	*(outBuf + 5) = (unsigned char)*(block_p + 13);
	*(outBuf + 6) = (unsigned char)*(block_p + 14);
	*(outBuf + 7) = (unsigned char)*(block_p + 15);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 16);
	*(outBuf + 1) = (unsigned char)*(block_p + 17);
	*(outBuf + 2) = (unsigned char)*(block_p + 18);
	*(outBuf + 3) = (unsigned char)*(block_p + 19);
	*(outBuf + 4) = (unsigned char)*(block_p + 20);
	*(outBuf + 5) = (unsigned char)*(block_p + 21);
	*(outBuf + 6) = (unsigned char)*(block_p + 22);
	*(outBuf + 7) = (unsigned char)*(block_p + 23);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 24);
	*(outBuf + 1) = (unsigned char)*(block_p + 25);
	*(outBuf + 2) = (unsigned char)*(block_p + 26);
	*(outBuf + 3) = (unsigned char)*(block_p + 27);
	*(outBuf + 4) = (unsigned char)*(block_p + 28);
	*(outBuf + 5) = (unsigned char)*(block_p + 29);
	*(outBuf + 6) = (unsigned char)*(block_p + 30);
	*(outBuf + 7) = (unsigned char)*(block_p + 31);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 32);
	*(outBuf + 1) = (unsigned char)*(block_p + 33);
	*(outBuf + 2) = (unsigned char)*(block_p + 34);
	*(outBuf + 3) = (unsigned char)*(block_p + 35);
	*(outBuf + 4) = (unsigned char)*(block_p + 36);
	*(outBuf + 5) = (unsigned char)*(block_p + 37);
	*(outBuf + 6) = (unsigned char)*(block_p + 38);
	*(outBuf + 7) = (unsigned char)*(block_p + 39);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 40);
	*(outBuf + 1) = (unsigned char)*(block_p + 41);
	*(outBuf + 2) = (unsigned char)*(block_p + 42);
	*(outBuf + 3) = (unsigned char)*(block_p + 43);
	*(outBuf + 4) = (unsigned char)*(block_p + 44);
	*(outBuf + 5) = (unsigned char)*(block_p + 45);
	*(outBuf + 6) = (unsigned char)*(block_p + 46);
	*(outBuf + 7) = (unsigned char)*(block_p + 47);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 48);
	*(outBuf + 1) = (unsigned char)*(block_p + 49);
	*(outBuf + 2) = (unsigned char)*(block_p + 50);
	*(outBuf + 3) = (unsigned char)*(block_p + 51);
	*(outBuf + 4) = (unsigned char)*(block_p + 52);
	*(outBuf + 5) = (unsigned char)*(block_p + 53);
	*(outBuf + 6) = (unsigned char)*(block_p + 54);
	*(outBuf + 7) = (unsigned char)*(block_p + 55);
	
	outBuf += row_offset;
	
	*outBuf = (unsigned char)*(block_p + 56);
	*(outBuf + 1) = (unsigned char)*(block_p + 57);
	*(outBuf + 2) = (unsigned char)*(block_p + 58);
	*(outBuf + 3) = (unsigned char)*(block_p + 59);
	*(outBuf + 4) = (unsigned char)*(block_p + 60);
	*(outBuf + 5) = (unsigned char)*(block_p + 61);
	*(outBuf + 6) = (unsigned char)*(block_p + 62);
	*(outBuf + 7) = (unsigned char)*(block_p + 63);
}

//***************************************************************
void I263Decoder::YUY2Output (unsigned char *inpBuf, unsigned char *outBuf, long row_offset)
{
	long x_line, y_line, offsetLuma, offsetChroma;
	unsigned char	*y_out_ptr1;
	unsigned char	*u_out_ptr;
	unsigned char	*v_out_ptr;
	
	unsigned char	*y_in_ptr = (inpBuf) + (this -> y_plane_offs);
	unsigned char	*u_in_ptr = (inpBuf) + (this -> u_plane_offs);
	unsigned char	*v_in_ptr = (inpBuf) + (this -> v_plane_offs);
	
	offsetLuma = PITCH - (this -> imageWidth);
	offsetChroma = PITCH - (this -> imageWidth >> 1);
	
	unsigned char	*y_out_ptr = outBuf;
	
	for (y_line = 0; y_line < this -> imageHeight; y_line++)
	{
		y_out_ptr1 = y_out_ptr;
		u_out_ptr = y_out_ptr1 + 1;
		v_out_ptr = y_out_ptr1 + 3;
		
		for (x_line = 0; x_line < this -> imageWidth; x_line++)
		{
			*y_out_ptr1 = *y_in_ptr;
			y_in_ptr++;
			y_out_ptr1 += 2;
			
			if ((x_line & 1) == 0) {
				*u_out_ptr = *u_in_ptr;
				*v_out_ptr = *v_in_ptr;
				u_in_ptr++;
				v_in_ptr++;
				u_out_ptr += 4;
				v_out_ptr += 4;
			}
		} // x_line
		
		if ((y_line & 1) == 0) {
			u_in_ptr -= PITCH;
			v_in_ptr -= PITCH;
		}
		
		y_in_ptr += offsetLuma;
		u_in_ptr += offsetChroma;
		v_in_ptr += offsetChroma;
		y_out_ptr += row_offset;		
	}
}

//***************************************************************
void I263Decoder::EdgePaddingRoutine (unsigned char *PlanePtr, bool IsLumaBuf)
{
	long			MB_size, width, height, cnt, cnt1, p1, p2;
	unsigned long	*wptr, *wptr1;
	unsigned char	*bufPtr, *rightPtr, *leftPtr;
	
	if (IsLumaBuf) {
		// choose the size of the macroblock (16 - luma, 8 - chroma)
		MB_size = 16;
	
		// choose the width and height of the picture
		width = this -> internalWidth;
		height = this -> internalHeight;
	}
	else {
		MB_size = 8;
		width = (this -> internalWidth) >> 1;
		height = (this -> internalHeight) >> 1;
	}
	
	// set the pointer to the last line of the plane
	bufPtr = PlanePtr + ((height - 1) * PITCH);
	
	// padding at the bottom	
	for (cnt = width; cnt != 0; cnt -= 8)
	{
		wptr = (unsigned long *)bufPtr;
		p1 = *wptr;
		p2 = *(wptr + 1);
		
		for (cnt1 = MB_size; cnt1 != 0; cnt1--)
		{
			wptr += PITCH >> 2;
			*(wptr) = p1;
			*(wptr + 1) = p2;
		}
		bufPtr += 8;
	}
	
	// padding at the left and right
	rightPtr = (unsigned char *)(wptr + 2);
	leftPtr = rightPtr - width;
	
	for (cnt = height + MB_size; cnt != 0; cnt--)
	{
		p1 = *leftPtr;
		p1 = p1 | (p1 << 8);
		p1 = p1 | (p1 << 16);
		p2 = *(rightPtr - 1);
		p2 = p2 | (p2 << 8);
		p2 = p2 | (p2 << 16);
		
		wptr = (unsigned long *)leftPtr;
		wptr1 = (unsigned long *)rightPtr;
		
		for (cnt1 = MB_size; cnt1 != 0; cnt1 -= 8)
		{
			*(wptr - 1) = p1;
			*(wptr - 2) = p1;
			
			*wptr1 = p2;
			*(wptr1 + 1) = p2;
			
			wptr -= 2;
			wptr1 += 2;
		}
		
		leftPtr -= PITCH;
		rightPtr -= PITCH;
	}
	
	// padding at the top
	bufPtr = PlanePtr - MB_size;
	
	for (cnt = width + MB_size + MB_size; cnt != 0; cnt -= 8)
	{
		wptr = (unsigned long *)bufPtr;
		p1 = *wptr;
		p2 = *(wptr + 1);
		
		for (cnt1 = MB_size; cnt1 != 0; cnt1--)
		{
			wptr -= PITCH >> 2;
			*(wptr) = p1;
			*(wptr + 1) = p2;
		}
		bufPtr += 8;
	}	
}