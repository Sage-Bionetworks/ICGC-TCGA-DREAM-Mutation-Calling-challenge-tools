#!/usr/bin/env python

import sys, os
import vcf

'''
Submission evaluation code for TCGA/ICGC/DREAM SMC
Adam Ewing, ewingad@soe.ucsc.edu
Requires PyVCF (https://github.com/jamescasbon/PyVCF)
'''

def match(subrec, trurec, vtype='SNV'):
    assert vtype in ('SNV', 'SV', 'INDEL')

    if vtype == 'SNV' and subrec.is_snp and trurec.is_snp:
        if subrec.POS == trurec.POS and subrec.REF == trurec.REF and subrec.ALT == trurec.ALT:
            return True

    if vtype == 'INDEL' and subrec.is_indel and trurec.is_indel:
        if subrec.POS == trurec.POS and subrec.REF == trurec.REF and subrec.ALT == trurec.ALT:
            return True

    if vtype == 'SV' and subrec.is_sv and trurec.is_sv:
        trustart, truend = expand_sv_ends(trurec)
        substart, subend = expand_sv_ends(subrec)

        # check for overlap
        if min(truend, subend) - max(trustart, substart) > 0:
            return True

    return False


def expand_sv_ends(rec):
    ''' assign start and end positions to SV calls using conf. intervals if present '''
    startpos, endpos = rec.start, rec.end
    assert rec.is_sv

    try:
        if rec.INFO.get('END'): # sometimes this is a list, sometimes it's an int
            if isinstance(rec.INFO.get('END'), list):
                endpos = int(rec.INFO.get('END')[0])
            if isinstance(rec.INFO.get('END'), int):
                endpos = int(rec.INFO.get('END'))

        if rec.INFO.get('CIPOS'):
            ci = map(int, rec.INFO.get('CIPOS'))
            if ci[0] < 0:
                startpos += ci[0]

        if rec.INFO.get('CIEND'):
            ci = map(int, rec.INFO.get('CIEND')) 
            if ci[0] > 0:
                endpos += ci[0]

    except TypeError as e:
        sys.stderr.write("error expanding sv interval: " + str(e) + " for record: " + str(rec) + "\n")

    if startpos > endpos:
        endpos, startpos = startpos, endpos

    return startpos, endpos


def relevant(rec, vtype, ignorechroms):
    ''' Return true if a record matches the type of variant being investigated '''
    rel = (rec.is_snp and vtype == 'SNV') or (rec.is_sv and vtype == 'SV') or (rec.is_indel and vtype == 'INDEL')

    # 'ignore' types are elways excluded
    if rec.INFO.get('SVTYPE'):
        if rec.INFO.get('SVTYPE') in ('IGN', 'MSK'):
            rel = False 

    return rel and (ignorechroms is None or rec.CHROM not in ignorechroms)


def passfilter(rec):
    ''' Return true if a record is unfiltered or has 'PASS' in the filter field (pyvcf sets FILTER to None) '''
    if rec.FILTER is None or rec.FILTER == '.' or not rec.FILTER:
        return True
    return False


def mask(rec, vcfh, truchroms, debug=False):
    ''' mask calls in IGN/MSK regions '''
    if rec.CHROM in truchroms:
        # all calls are ignored in MSK regions
        for overlap_rec in vcfh.fetch(rec.CHROM, rec.POS-1, rec.POS):
            if overlap_rec.INFO.get('SVTYPE') == 'MSK':
                if debug:
                    print "DEBUG: submitted:", str(rec), "overlaps:", str(overlap_rec)
                return True

        # SNV and INDEL calls are ignored in IGN regions:
        for overlap_rec in vcfh.fetch(rec.CHROM, rec.POS-1, rec.POS):
            if (rec.is_snp or rec.is_indel) and overlap_rec.INFO.get('SVTYPE') == 'IGN':
                return True

    return False


def countrecs(submission, vtype='SNV', ignorechroms=None):
    ''' return number of records in submission '''
    
    assert vtype in ('SNV', 'SV', 'INDEL')
    subvcfh = vcf.Reader(filename=submission)

    subrecs = 0

    for subrec in subvcfh:
        if passfilter(subrec):
            if (ignorechroms is None or subrec.CHROM not in ignorechroms):
                if subrec.is_snp and vtype == 'SNV':
                    #if not svmask(subrec, truvcfh, truchroms):
                    subrecs += 1
                if subrec.is_sv and vtype == 'SV':
                    subrecs += 1
                if subrec.is_indel and vtype == 'INDEL':
                    subrecs += 1

    return subrecs


def evaluate(submission, truth, vtype='SNV', ignorechroms=None):
    ''' return stats on sensitivity, specificity, balanced accuracy '''

    assert vtype in ('SNV', 'SV', 'INDEL')
    subvcfh = vcf.Reader(filename=submission)
    truvcfh = vcf.Reader(filename=truth)

    tpcount = 0
    fpcount = 0
    subrecs = 0
    trurecs = 0

    truchroms = {}

    ''' store list of truth records, otherwise the iterator needs to be reset '''
    trulist = [trurec for trurec in truvcfh]

    ''' count records in truth vcf, track contigs/chromosomes '''
    for trurec in trulist:
        if relevant(trurec, vtype, ignorechroms):
            trurecs += 1
            truchroms[trurec.CHROM] = True

    ''' subtract masked variants from truth '''
    trumasked = 0
    submasked = 0

    for trurec in trulist:
        if relevant(trurec, vtype, ignorechroms) and mask(trurec, truvcfh, truchroms):
            trurecs -= 1
            trumasked += 1

    used_truth = {} # keep track of 'truth' sites used, they should only be usable once
    used_bnd_mates = {} # if submitters use MATEID in their BND calls we can 'tie' them together

    ''' parse submission vcf, compare to truth '''
    for subrec in subvcfh:
        if relevant(subrec, vtype, ignorechroms) and not mask(subrec, truvcfh, truchroms):
            if passfilter(subrec):
                if subrec.is_snp and vtype == 'SNV':
                    subrecs += 1
                if subrec.is_sv and vtype == 'SV':
                    subrecs += 1
                if subrec.is_indel and vtype == 'INDEL':
                    subrecs += 1

            matched = False
            SV_BND_multimatch = False 

            startpos, endpos = subrec.start, subrec.end

            if vtype == 'SV' and subrec.is_sv:
                startpos, endpos = expand_sv_ends(subrec)
                if subrec.INFO.get('MATEID'):
                    used_bnd_mates[subrec.INFO.get('MATEID')[0]] = True
            try:
                if passfilter(subrec):
                    for trurec in truvcfh.fetch(subrec.CHROM, startpos, end=endpos):
                        # if using BND notation, don't penalize multiple BND records matching one truth interval
                        if str(trurec) in used_truth: 
                            if vtype == 'SV' and subrec.INFO.get('SVTYPE') and subrec.INFO.get('SVTYPE') == 'BND':
                                SV_BND_multimatch = True

                        if match(subrec, trurec, vtype=vtype) and str(trurec) not in used_truth:
                            matched = True
                            used_truth[str(trurec)] = True

                if not matched and subrec.ID in used_bnd_mates:
                    SV_BND_multimatch = True

            except ValueError as e:
                sys.stderr.write("Warning: " + str(e) + "\n")

            if matched:
                tpcount += 1
            else:
                if passfilter(subrec): 
                    if not SV_BND_multimatch: # don't penalize BND multi-matches to truth intervals
                        fpcount += 1 

        else:
            if relevant(subrec, vtype, ignorechroms):
                submasked += 1

    print "tpcount, fpcount, subrecs, submasked, trurecs, trumasked:"
    print tpcount, fpcount, subrecs, submasked, trurecs, trumasked

    sensitivity = float(tpcount) / float(trurecs)
    precision   = float(tpcount) / float(tpcount + fpcount)
    specificity = 1.0 - float(fpcount) / float(subrecs)
    balaccuracy = (sensitivity + specificity) / 2.0

    return sensitivity, specificity, balaccuracy


if __name__ == '__main__':
    if len(sys.argv) == 4 or len(sys.argv) == 5:
        subvcf, truvcf, evtype = sys.argv[1:4]

        chromlist = None
        if len(sys.argv) == 5:
            chromlist = sys.argv[4].split(',')

        if not subvcf.endswith('.vcf') and not subvcf.endswith('.vcf.gz'):
            sys.stderr.write("submission VCF filename does not enc in .vcf or .vcf.gz\n")
            sys.exit(1)

        if not os.path.exists(truvcf + '.tbi'):
            sys.stderr.write("truth VCF does not appear to be indexed. bgzip + tabix index required.\n")
            sys.exit(1)

        if evtype not in ('SV', 'SNV', 'INDEL'):
            sys.stderr.write("last arg must be either SV, SNV, or INDEL\n")
            sys.exit(1)

        result = evaluate(subvcf, truvcf, vtype=evtype, ignorechroms=chromlist)

        print "sensitivity, specificity, balanced accuracy: " + ','.join(map(str, result))

    else:
        print "standalone usage for testing:", sys.argv[0], "<submission VCF> <truth VCF (tabix-indexed)> <SV, SNV, or INDEL> [ignore chrom list (comma-delimited, optional)]"
