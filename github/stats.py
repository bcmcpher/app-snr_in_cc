#!/usr/bin/env python

import logging
import shutil
import numpy as np
import nibabel as nib
import sys
import os
import json
from scipy.ndimage.morphology import binary_dilation

from dipy.io import read_bvals_bvecs
from dipy.core.gradients import gradient_table
from dipy.segment.mask import median_otsu
from dipy.reconst.dti import TensorModel
        
from dipy.segment.mask import segment_from_cfa
from dipy.segment.mask import bounding_box

from dipy.workflows.workflow import Workflow


class SNRinCCFlow(Workflow):
    
    @classmethod
    def get_short_name(cls):
        return 'snrincc'
    
    def run(self, data_file, data_bvals, data_bvecs, mask=None, bbox_threshold=(0.6, 1, 0, 0.1, 0, 0.1), out_dir = '', out_file='product.json'):
        """
        Parameters
        ----------
        data_file : string
            Path to the dwi.nii.gz file. This path may contain wildcards to
            process multiple inputs at once.
        data_bvals : string
            Path of bvals.
        data_bvecs : string
            Path of bvecs.
        mask : string, optional
            Path of mask if needed. (default None)
        bbox_threshold : string, optional
            for ex. [0.6,1,0,0.1,0,0.1], converted to a tuple; threshold for bounding box. (default (0.6, 1, 0, 0.1, 0, 0.1))
        out_dir : string, optional
            Where the resulting file will be saved. (default '')
        out_file : string, optional
            Name of the result file to be saved. (default 'product.json')
        """

        if bbox_threshold != (0.6, 1, 0, 0.1, 0, 0.1):
            b = bbox_threshold.replace("[","")
            b = b.replace("]","")
            b = b.split(",")
            for i in range(len(b)):
                b[i] = float(b[i])
            bbox_threshold = tuple(b)

        io_it = self.get_io_iterator()
        
        for data_path, data_bvals_path, data_bvecs_path, out_path in io_it:
           
            img = nib.load('{0}'.format(data_path))
            bvals, bvecs = read_bvals_bvecs('{0}'.format(data_bvals_path), '{0}'.format(data_bvecs_path))
            gtab = gradient_table(bvals, bvecs)
            
            data = img.get_data()
            affine = img.affine

            if mask == None:
                logging.info('Computing brain mask...')
                b0_mask, mask = median_otsu(data)

            logging.info('Computing tensors...')
            tenmodel = TensorModel(gtab)
            tensorfit = tenmodel.fit(data, mask=mask)

            logging.info('Computing worst-case/best-case SNR using the corpus callosum...')
            threshold = bbox_threshold
            
            if affine.shape == (4,4):
                CC_box = np.zeros_like(data[..., 0])
            elif affine.shape == (3,3): # is this right/okay?
                CC_box = np.zeros_like(data)
            
            mins, maxs = bounding_box(mask)
            mins = np.array(mins)
            maxs = np.array(maxs)
            diff = (maxs - mins) // 4
            bounds_min = mins + diff
            bounds_max = maxs - diff

            CC_box[bounds_min[0]:bounds_max[0],
                   bounds_min[1]:bounds_max[1],
                   bounds_min[2]:bounds_max[2]] = 1

            mask_cc_part, cfa = segment_from_cfa(tensorfit, CC_box, threshold,
                                                 return_cfa=True)
                                                 
            cfa_img = nib.Nifti1Image((cfa*255).astype(np.uint8), affine)
            mask_cc_part_img = nib.Nifti1Image(mask_cc_part.astype(np.uint8), affine)
            nib.save(mask_cc_part_img, 'cc.nii.gz')

            mean_signal = np.mean(data[mask_cc_part], axis=0)

            mask_noise = binary_dilation(mask, iterations=10)
            mask_noise[..., :mask_noise.shape[-1]//2] = 1
            mask_noise = ~mask_noise
            mask_noise_img = nib.Nifti1Image(mask_noise.astype(np.uint8), affine)
            noise_std = np.std(data[mask_noise, :])
            logging.info('Noise standard deviation sigma= ' + str(noise_std))

            idx = np.sum(gtab.bvecs, axis=-1) == 0
            gtab.bvecs[idx] = np.inf
            axis_X = np.argmin(np.sum((gtab.bvecs-np.array([1, 0, 0])) **2, axis=-1))
            axis_Y = np.argmin(np.sum((gtab.bvecs-np.array([0, 1, 0])) **2, axis=-1))
            axis_Z = np.argmin(np.sum((gtab.bvecs-np.array([0, 0, 1])) **2, axis=-1))

            SNR_output = []
            SNR_directions = []
            for direction in [0, axis_X, axis_Y, axis_Z]:
                SNR = mean_signal[direction]/noise_std
                if direction == 0 :
                    logging.info("SNR for the b=0 image is :" + str(SNR))
                else :
                    logging.info("SNR for direction " + str(direction) + " " + str(gtab.bvecs[direction]) + "is :" + str(SNR))
                    SNR_directions.append(direction)
                SNR_output.append(SNR)
            
            data = []
            data.append({
                        'data': str(SNR_output[0]) + ' ' + str(SNR_output[1]) + ' ' + str(SNR_output[2]) + ' ' + str(SNR_output[3]),
                        'directions': 'b0' + ' ' + str(SNR_directions[0]) + ' ' + str(SNR_directions[1]) + ' ' + str(SNR_directions[2])
                        })
            
            with open(os.path.join(out_dir,out_file), 'w') as myfile:
                json.dump(data, myfile)
