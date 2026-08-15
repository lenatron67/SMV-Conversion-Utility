/*
 *  i263VideoHeaders.cpp
 *	Picture, Macroblock and GOB headers decoding
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
#include "BitStream.h"
#include "I263data.h"

//**************************************************************
long I263Decoder::DecodePictureHeader (BitStream *bs)
{
	long			cnt, format, aspect_ratio;
	unsigned long	psc;
	
	/* read picture start code (PSC) */
	psc = GetBits (bs, 22);
	
	/* search for PSC ! */
	for (cnt = 0; psc != 32 && cnt < 256; cnt++)
		psc = (psc << 1 & 0x3FFFFF) | GetBits (bs, 1);
		
	/* error: psc not found ! */
	if (psc != 32)
		return (-1);
		
	/* temporal reference */
	this -> old_tr = this -> pic_tr;
	this -> pic_tr = GetBits (bs, 8);
	
	/* marker */
	if (GetBits (bs, 1) != 1)
		return (-1);// invalid marker bit
		
	/* h263 id */
	if (GetBits (bs, 1) != 0)
		return (-401);// invalid h263 id
		
	this -> split_screen = GetBits (bs, 1);
	this -> doc_camera = GetBits (bs, 1);
	this -> pic_freez_rel = GetBits (bs, 1);
	
	/* picture format */
	format = GetBits (bs, 3);
	if (format == 0 || format == 6)
		return (-1);// invalid picture type
	
	this -> old_pic_fmt = this -> pic_fmt;
	this -> pic_fmt = format;
	
	if (this -> varC2 && (this -> pic_fmt != format) && format != PLUSPTYPE)
		return (-1);// invalid picture type
	if (this -> varC2 || format != PLUSPTYPE)
		this -> varC2 = 1;
	
	this -> pic_type = !(GetBits (bs, 1)); // 1 - INTRA, 0 - INTER !!!
	this -> umv_mode = GetBits (bs, 1); // unrestricted MV, varB8
	this -> sac_mode = GetBits (bs, 1); // SAC unused, varBA
	this -> adv_pred_mode = GetBits (bs, 1); // advanced prediction mode, varBC
	this -> pb_frames = GetBits (bs, 1); // pb frames mode: 0 - I or P, 1 - PB frames used
	
	if ((this -> pic_type == INTRA) && (this -> pb_frames == 1))
		return (-1);
		
	if (format == PLUSPTYPE)
	{
		format = GetBits (bs, 3);
		if (format == 0 || format == PLUSPTYPE)
			return (-1);// invalid picture type
		this -> pic_fmt = format;
		if (this -> varC2 && format != this -> old_pic_fmt)
			return (-1);// invalid picture type
		this -> varC2 = 1;
		
		// unused flags, must be 0 ! (varC4 & varC6)
		if (GetBits (bs, 2) != 0)
			return (-1);
			
		this -> DF_mode = GetBits (bs, 1);// deblocking filter, varC8
		
		// unused flag, varCA
		if (GetBits (bs, 1) != 0)
			return (-1);
			
		this -> impr_pb_frames = GetBits (bs, 1);// Improved PB-frames mode (see Draft H.263+ 1996)
		
		// unused flags, must be 0 ! (varCE, varD0, varD2, varD4, varD6)
		if (GetBits (bs, 5) != 0)
			return (-1);
		
		// marker ?
		if (GetBits (bs, 5) != 1)
			return (-1);
	}
	else
	{
		this -> impr_pb_frames = 0;
		this -> DF_mode = 0;
	}
	
	if (this -> pic_fmt == CUSTOM_FMT)
	{
		aspect_ratio = GetBits (bs, 4); // aspect ratio code
		
		if ((long)((GetBits (bs, 9) << 2) + 4) != this -> imageWidth)
			return (-1); // invalid width
			
		// Prevent start code emulation
		if (GetBits (bs, 1) != 1)
			return (-1);
		
		if ((long)((GetBits (bs, 9) << 2) + 4) != this -> imageHeight)
			return (-1); // invalid height
			
		switch (aspect_ratio) // aspect ratio code
		{
			case 1:
				this -> aspect_width =
				this -> aspect_height = 1;
				break;
			
			case 2:
				this -> aspect_width = 12;
				this -> aspect_height = 11;
				break;
			
			case 3:
				this -> aspect_width = 10;
				this -> aspect_height = 11;
				break;
			
			case 15: // must be 15 !!!!
				this -> aspect_width = GetBits (bs, 8);
				if (!this -> aspect_width)
					return (-1);
				this -> aspect_height = GetBits (bs, 8);
				if (!this -> aspect_height)
					return (-1);
				break;
			
			default:
				return (-1);
		}//switch
	}	
	
	this -> pquant = GetBits (bs, 5); // pquant, varA8
	
	// unused, check the GOB-header
	this -> CPM_mode = GetBits (bs, 1);
	if (this -> CPM_mode)
		return (-1);
		
	if (this -> pb_frames == 1)
	{
		this -> TRB = GetBits (bs, 3); // varA4
		this -> dbquant = GetBits (bs, 2); // varAC
	}
	else {
		this -> TRB = 0; 
		this -> dbquant = 0;
	}
	
	// skip PEI
	while (GetBits (bs, 1))
        GetBits (bs, 8);
		
	return 0;
}

//**************************************************************
long I263Decoder::DecodeGOBHeader (BitStream *bs, long gob_num)
{
	long	gob_ID, cnt, gfid;
	
	BitStream	bs_copy = *bs; // make a local copy of the BitStream obj to restore later
	
	this -> ghdr_found = 0; // flag = no GOB header was found
	
	if (gob_num == 0)
		this -> varF0 = 0;
	else
	{
		/* GOB start code */
		gob_ID = GetBits (&bs_copy, 17);
		
		// search for GOB start code !
		for (cnt = 0; gob_ID != 1 && cnt < 7; cnt++)
			gob_ID = (gob_ID << 1 & 0x1FFFF) | GetBits (&bs_copy, 1);
	
		if (gob_ID == 1)
			this -> ghdr_found = 1; // o.k. we have a header
	}//if
	
	if ((this -> ghdr_found) == 0)
	{
		this -> gob_num = gob_num; // varE4
		if (this -> varF0 == 0)
			this -> gfid = 0;
		
		this -> gquant = this -> pquant;
	}
	else // 00BE48B7
	{
		*bs = bs_copy;// modify the data position
		this -> gob_num = GetBits (bs, 5);
		if (this -> CPM_mode)
			return (-1);
		
		gfid = GetBits (bs, 2);
		if (this -> varF0 && this -> gfid != gfid)
			return (-1);
			
		this -> gfid = gfid;
		this -> varF0 = 1;
		this -> gquant = this -> pquant = GetBits (bs, 5);
	}
	
	return (0);
}

//**************************************************************
long I263Decoder::DecodeMacroblockHeader (BitStream *bs, BMBlock *bmbPtr)
{
	bool			stuffing_found;
	unsigned long	vlc, sym;
	long			mb_type;
	bool			cbpb_present, mvdb_present;
	
	do
	{
		if (this -> pic_type == INTRA)
			this -> MB_coded = true;
		else
			this -> MB_coded = !(GetBits (bs, 1)); // "COD"-bit
		
		// skip this MB
		if (this -> MB_coded == false) {
			// reset the information for the B-macroblock
			bmbPtr -> pmb_type = 0;
			bmbPtr -> cbpb = 0;
			bmbPtr -> mvdb_h = 0;
			bmbPtr -> mvdb_v = 0;
			bmbPtr -> forward_pred = 0;
			
			return (0);
		}
	
		stuffing_found = false;
	
		if (this -> pic_type == INTRA)
		{
			vlc = ShowBits (bs, 6);
			sym = mcbpc_vlc_intra_tab[vlc]; // decode using the LUT
			SkipBits (bs, sym & 0xFF);
		
			if (vlc == 0) {
				// TODO: error detection !
				stuffing_found = true;
			}
		}
		else {
			// MCBPC for INTER-pictures
			vlc = ShowBits (bs, 9);
			sym = mcbpc_vlc_inter_tab[vlc]; // decode using the LUT
			SkipBits (bs, sym & 0xFF);
		
			if (vlc == 1)
				stuffing_found = true;
			else if (vlc == 0)
				return (-401); // error correction indicator !
		}
	} while (stuffing_found);
	
	mb_type = (sym & 0x1C00) >> 10;
	if (this -> pic_type && mb_type != MB_INTRA && mb_type != MB_INTRA_Q)
		return (-1);
	this -> MB_type = mb_type;
	
	// set the MB type of the P-picture
	bmbPtr -> pmb_type = mb_type;
	
	// decode the cbpc
	this -> cbpc = (sym & 0x300) >> 8;
	
	cbpb_present = false;
	mvdb_present = false;
	this -> cbpb = 0;
	bmbPtr -> cbpb = 0;
	
	if (this -> pb_frames == 1)
	{
		bmbPtr -> forward_pred = false;
		
		if (this -> impr_pb_frames)
		{
			// MODB coding for Improved PB-frames mode (Draft H.263+ 1996, Annex M.4)
			if (GetBits (bs, 1) == 1)
			{
				cbpb_present = GetBits (bs, 1);
				if (cbpb_present)
					mvdb_present = !(GetBits (bs, 1));
				else
					mvdb_present = true;
			}
			
			if (mvdb_present)
				bmbPtr -> forward_pred = true;			
		}
		else {
			// standard H.263 MODB
			mvdb_present = GetBits (bs, 1);
			if (mvdb_present)
				cbpb_present = GetBits (bs, 1);
		}
	}
	
	// decode the CBPB
	if (cbpb_present) {
		this -> cbpb = GetBits (bs, 6);
		bmbPtr -> cbpb = this -> cbpb;
	}
		
	// decode the CBPY using LUT
	sym = cbpy_vlc_tab[ShowBits (bs, 6)];
	SkipBits (bs, sym & 0xFF);
	if (sym == 0)
		return (-1);
	
	this -> cbpy = (mb_type >= MB_INTRA) ? ((sym >> 12) & 0xF) : ((sym >> 8) & 0xF);
	
	if (mb_type != MB_INTER_Q && mb_type != MB_INTRA_Q)
		this -> dquant_diff = 0;
	else {
		this -> dquant_diff = dquant_diff_tab[GetBits (bs, 2)];
		this -> gquant += this -> dquant_diff;
		// TODO: insert the clipping function !!!
		this -> pquant = this -> gquant;
	}
	
	// zero the MVD1
	this -> mvd1_h = 0;
	this -> mvd1_v = 0;
	
	// decode the MVs
	if (mb_type <= MB_INTER4V || (this -> pb_frames) == 1)
	{
		// decode the horizontal component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd1_h = (sym >> 8) & 0xFF;
		
		// decode the vertical component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd1_v = (sym >> 8) & 0xFF;
	}
	
	if ((this -> adv_pred_mode || this -> DF_mode) && mb_type == MB_INTER4V)
	{
		// MVD 2 - decode the horizontal component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd2_h = (sym >> 8) & 0xFF;
		
		// decode the vertical component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd2_v = (sym >> 8) & 0xFF;
		
		// MVD 3 - decode the horizontal component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd3_h = (sym >> 8) & 0xFF;
		
		// decode the vertical component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd3_v = (sym >> 8) & 0xFF;
		
		// MVD 4 - decode the horizontal component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd4_h = (sym >> 8) & 0xFF;
		
		// decode the vertical component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvd4_v = (sym >> 8) & 0xFF;
	} // if adv_pred_mode || DF_mode ...
	
	bmbPtr -> mvdb_h = 0;
	bmbPtr -> mvdb_v = 0;
	
	if (mvdb_present) {
		// MVDB - decode the horizontal component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvdb_h = (sym >> 8) & 0xFF;
		bmbPtr -> mvdb_h = this -> mvdb_h;
		
		// decode the vertical component
		vlc = ShowBits (bs, 13);
		sym = (vlc >= 192) ? (mvd_vlc_tab1[vlc >> 5]) : mvd_vlc_tab2[vlc];
		if (sym == 0)
			return (-1);
		SkipBits (bs, sym & 0xFF);
		this -> mvdb_v = (sym >> 8) & 0xFF;
		bmbPtr -> mvdb_v = this -> mvdb_v;
	}
 		
	return (0);
}
