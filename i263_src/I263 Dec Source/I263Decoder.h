/*
 *  I263Decoder.h
 *	The definition of the I.263 decoder class
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

#ifndef I263_DEC
#define I263_DEC

#include "BitStream.h"

// picture types
#define	INTRA	1
#define	INTER	0

// other constants
#define	PLUSPTYPE	7
#define CUSTOM_FMT	6

#define MAX_WIDTH	352
#define MAX_HEIGHT	288
// PITCH = MAX_WIDTH + 2 EXTRA MBs for padding
// such use of the maximum picture width simplifies the decoder!
#define	PITCH		384

// macroblock types
#define MB_NOT_CODED	-1
#define MB_INTER		0
#define MB_INTER_Q		1
#define MB_INTER4V		2
#define MB_INTRA		3
#define MB_INTRA_Q		4

// this structure is needed to decode the mc
typedef struct Block
{
	bool	is_intra;// true = block is INTRA coded
	bool	tcoef_present;// TCOEFF present
	long	mv_h; // horizontal vector component
	long	mv_v; // vertical vector component
	long	out_offset; // offset in the output plane
	long	number; // number of this block
} Block;

// this structure is needed to decode B-macroblocks
typedef struct BMBlock
{
	char	pmb_type; // type of the corresponding P-macroblock
	char	cbpb; // cbpb value for this MB
	char	mvdb_h; // horizontal vector component
	char	mvdb_v; // vertical vector component
	bool	forward_pred; // true - forward predicted, false - bidirectionally predicted
} BMBlock;

void CopyBlock (unsigned char *src, short *dst);
void HorizontalHalfpelInterpolation (unsigned char *src, short *dst);
void VerticalHalfpelInterpolation (unsigned char *src, short *dst);
void HorizAndVertHalfpelInterpolation (unsigned char *src, short *dst);

class I263Decoder
{
	public:
		I263Decoder();
		~I263Decoder();
		
		long Initialize (long width, long height);
		long DecodeFrame (unsigned char *inputData, long framesize, unsigned char *outData, long row_offset);
		long DecodePictureHeader (BitStream *bs);
		long DecodeGOBHeader (BitStream *bs, long gob_num);
		long DecodeMacroblockHeader (BitStream *bs, BMBlock *bmbPtr);
		void DecodeMacroblockVectors (Block *block_ptr, long x, long y);
		void DecodeVectorsB (BMBlock *bmbPtr, Block *block_ptr, long *MB_MVF_h, long *MB_MVF_v,
										long *MB_MVB_h, long *MB_MVB_v);
		long DecodeMacroblock (BitStream *bs, long MB_x, long MB_y, Block *block_ptr, short *coefsBuf);
		void ReconstructMacroblock (long MB_x, long MB_y, Block *block_ptr, short *coefsBuf, long edgeFlags);
		void ReconstructMacroblockB (BMBlock *bmbPtr, Block *block_ptr, short *coefsBuf, long edgeFlags);
		long DecodeBlock (BitStream *bs, short *coefs_buf, bool intradc_coded, bool tcoef_coded, long quant);
		long DecodePicture (BitStream *bs);
		
		void OutputBlock (short *block_p, unsigned char *outBuf, long row_offset);
		void YUY2Output (unsigned char *inpBuf, unsigned char *outBuf, long row_offset);
		void EdgePaddingRoutine (unsigned char *PlanePtr, bool IsLumaBuf);
		void UMVLimitVector (long  blkNum, long edgeFlags, long *horVComp, long *vertVComp);
		//void CopyBlock (short *dst, unsigned char *src, long row_offset);
		void AddErrorTerm (short *dst, short *src);
		void OverlappedMC (Block *block_ptr, long blockNum, long edgeFlags, short *outPred, long *surrBlocks);
		void BidirectionalPrediction (long blockNum, long MVB_h, long MVB_v, unsigned char *prec, short *fwPred);
		
		void InitBlockEdgeFilter (void);
		void BlockEdgeFilter (unsigned char *y_ptr, unsigned char *u_ptr, unsigned char *v_ptr);
		void HorizontalFiltering (unsigned char *planePtr, long width, long height, long sizefactor);
		void VerticalFiltering (unsigned char *planePtr, long width, long height, long sizefactor);
		
		void IDCT (short *block);
		void idctrow(short *blk);
		void idctcol(short *blk);
		
	private:
		long	imageWidth;
		long	imageHeight;
		long	internalWidth;
		long	internalHeight;
		long	MB_width;
		long	MB_height;
		long	frame_size;
		
		// decoder buffers
		unsigned char	*yv12_buf0_ptr;
		unsigned char	*yv12_buf1_ptr;
		unsigned char	*curr_frm_buf_ptr;
		unsigned char	*prev_frm_buf_ptr;
		unsigned char	*b_frm_buf_ptr;
		long			y_plane_offs;
		long			u_plane_offs;
		long			v_plane_offs;
		short			*advPredBuf;
		
		short	*coefsBuf;
		Block	*blocks_buffer;
		BMBlock	*bmb_buffer;
		
		// clipping tables
		short	*iclip;
		short	*iclp;
		short	*pclip;
		short	*pclp;
		
		// picture header
		long	old_tr;
		long	pic_tr;
		long	split_screen;	// vars + B2
		long	doc_camera;		// vars + B0
		long	pic_freez_rel;	// vars + B4
		long	old_pic_fmt;
		long	pic_fmt;
		long	varC2;
		long	pic_type; // 1 - INTRA, 0 - INTER
		long	umv_mode; // varB8
		long	sac_mode; // unused
		long	adv_pred_mode; // varBC
		long	pb_frames;
		long	DF_mode;  // deblocking filter varC8
		long	impr_pb_frames;	// USED ! Improved PB-frames mode (Juni 1996)
		long	aspect_width;
		long	aspect_height;
		long	pquant; // varA8
		long	CPM_mode; // unused
		long	TRB; // varA4
		long	dbquant; // varAC
		
		// gob header
		long	ghdr_found;
		long	varF0;
		long	gob_num;
		long	gfid;
		long	gquant; // varEC
		
		// macroblock header
		bool	MB_coded;
		long	MB_type;
		long	cbpc;
		long	cbpb;
		long	cbpy;
		long	dquant_diff;
		char	mvd1_h; // var110
		char	mvd1_v; // var111
		char	mvd2_h; // var10A
		char	mvd2_v; // var10B
		char	mvd3_h; // var10C
		char	mvd3_v; // var10D
		char	mvd4_h; // var10E
		char	mvd4_v; // var10F
		char	mvdb_h; // var108
		char	mvdb_v; // var109
		
		// debug vars
		long	coefSum;
		long	MvhSum;
		long	MvvSum;
		long	MVF_h_sum;
		long	MVF_v_sum;
		long	MVB_h_sum;
		long	MVB_v_sum;
		
		// block edge filter
		long			FilterTab[64][32];
		bool			MB_cod[20][32];
		unsigned char	MB_quant[18][32];
};

#endif