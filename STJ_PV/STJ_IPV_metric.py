import numpy as np
import pdb
import os
import collections
from scipy import interpolate
from scipy.signal import argrelmin, argrelmax, argrelextrema
import scipy.io as io
from numpy.polynomial import chebyshev as cby
import copy as copy
import matplotlib.pyplot as plt
import matplotlib as mpl
from scipy.stats import mstats, t, linregress
from scipy import linalg
import math
from matplotlib.ticker import MultipleLocator
from datetime import date
# Dependent code
from STJ_PV_main import Directory
from general_plotting import draw_map_model
from general_functions import (openNetCDF4_get_data,
                               apply_mask_inf, MeanOverDim, FindClosestElem)
from calc_ipv import theta
import calc_ipv  # assigns th_levels_trop
from IPV_plots import Plotting, PlotCalendarTimeseries, PlotPC,plot_validation_for_paper 
from IPV_plots import make_u_plot,plot_thermap_trop_vs_pressure
# partial correlation code forked from
# https://gist.github.com/fabianp/9396204419c7b638d38f
from partial_corr import partial_corr


__author__ = "Penelope Maher"

base = os.environ['BASE']
data_dir = '{}/Data'.format(base)
#data_out_dir = '{}/Data'.format(base)
data_out_dir = '/scratch/pm366/Data/ERA_INT/'

if not os.path.exists(data_dir):
    print('CREATING DATA DIRECTORY: {}'.format(data_dir))
    os.system('mkdir -p {}'.format(data_dir))

if not os.path.exists(data_out_dir):
    print('CREATING DATA OUT DIRECTORY: {}'.format(data_out_dir))
    os.system('mkdir -p {}'.format(data_out_dir))


class Method_2PV_STJ(object):
    'Input data of the form self.IPV[time, theta, lat, lon]'

    def __init__(self, IPV_data, diri, u_fname):

        self.lat = IPV_data['lat']
        self.lon = IPV_data['lon']
        self.theta_lev = IPV_data['theta_lev']

        self.IPV = IPV_data['IPV']
        self.u = IPV_data['uwnd']

        self.diri = diri

        self.TropH = IPV_data['TropH']
        self.TropH_p = IPV_data['TropH_p']
        self.TropH_temp = IPV_data['TropH_temp']
        self.diri = Directory()
        self.u_fname = u_fname
        self.yy = 37
 
    def PrepForAlgorithm(self):

        # Prep step 1: Define lat interpolate IPV to
        lat_increment = 0.2
        self.lat_SH = np.arange(-90, 0 + lat_increment, lat_increment)
        self.lat_NH = np.arange(0, 90 + lat_increment, lat_increment)

        # Prep step 2: Define theta interpolate IPV to
        # using a smaller domain to avoid areas where 2.0 PV is not defined
        self.theta_domain = np.arange(310, 401, 1.0)

        # Prep Step 3: mask the array so inf can be managed
        self.IPV = apply_mask_inf(self.IPV)

    def Prep_lon_slices(self):

        lon_slices_num = 8.
        if self.lon.min() == 0.0:
            lon_slice = np.arange(0, 360, 360. / lon_slices_num)
        else:
            print('Check lon data.')
            pdb.set_trace()

        elem_loc = np.zeros(lon_slices_num)
        for i in range(int(lon_slices_num)):
            elem_loc[i] = FindClosestElem(lon_slice[i], self.lon, 0)[0]

        self.lon_loc = elem_loc

    def get_phi_theta_2PV(self, time_loop, hemi, slide_method, lon_elem):
        """
        Interpolation IPV using interpolate.interp2d for lat increment of 0.2 and
        theta of 1K between 310-400K
        """

        # Step 1: Take the zonal mean
        ipv_zonal = MeanOverDim(data=self.IPV[time_loop, :, :, :], dim=2)  # [theta, lat]

        # Step 2: Interpolate
        #  2.1 Prepare the function for interpolation
        if slide_method == 'zonal_mean':
            ipv_function = interpolate.interp2d(
                self.lat, self.theta_lev, ipv_zonal[:, :], kind='cubic')
        else:
            ipv_function = interpolate.interp2d(self.lat, self.theta_lev,
                                                self.IPV[time_loop, :, :, lon_elem],
                                                kind='cubic')
        #  2.1 interpolate onto new lat and theta
        ipv_domain = ipv_function(self.lat_hemi, self.theta_domain)

        # Step 3: Find the element location closest to +-2.0 PV line
        # - code for monotonic increase theta and phi.
        # - actual_ipv_values is used to locic test the 2pv line
        # - line_2PV_elem is the element locations where the near-2.0 pv element 0ccurs.
        line_2PV_elem, actual_ipv_values = IPV_get_2PV(data=ipv_domain, pv_line=self.pv2,time_loop=time_loop)

        # remove zero from array and assign as a list
        line_2PV_list = line_2PV_elem[line_2PV_elem != 0].tolist()
       
        # Assign variables
        self.phi_2PV = self.lat_hemi[line_2PV_list]  # lat on 2 pv line
        self.theta_2PV = self.theta_domain           # theta value on 2 pv line
        # list of elements on the 2pv line
        self.line_2PV_list = line_2PV_elem
        # the values that are closest to the 2PV lines
        self.actual_ipv_values = actual_ipv_values
        self.ipv_domain = ipv_domain                 # fitted IPV data
        # zonal mean of original grid IPV
        self.ipv_zonal = ipv_zonal


    def PolyFit2PV_der(self, time_loop,deg_fit):
        """
        Calculate the chebyshev polynomial fit of the 2.0 PV line
        and its first two derivatives
        """

        # find the chebyshev polynomial fit
        theta_cby = cby.chebfit(self.phi_2PV, self.theta_2PV, deg_fit)

        # values of the fit
        self.theta_cby_val = cby.chebval(self.phi_2PV, theta_cby)

        # then differentiate dtheta_2PV/dy
        dtdphi_cby = cby.chebder(theta_cby)

        # values of the derivative d theta d phi
        self.dtdphi_val = cby.chebval(self.phi_2PV, dtdphi_cby)

        # second derivative
        d2tdphi2_cby = cby.chebder(dtdphi_cby)
        self.d2tdphi2_val = cby.chebval(self.phi_2PV, d2tdphi2_cby)

        # if time_loop == 73:
        #  testing = True
        # else:
        #  testing = False
        testing = False
        # test the poly fit
        if testing:
            Poly_testing(self.phi_2PV, self.theta_2PV, self.theta_cby_val,
                         self.dtdphi_val, self.d2tdphi2_val)
            pdb.set_trace()

    def unique_elements(self, hemi, time_loop):
        'Remove repeated pairs of theta and phi from 2 pv line.'

        remove_duplicat_lat = True
        if remove_duplicat_lat:

            # Removing repeated lats. These occur where very large gradient. Keeps the lowest theta level
            phi_2PV, phi_idx,counts = np.unique(self.phi_2PV, return_index=True,  return_counts=True)
            #pdb.set_trace()
            # Keep the median theta level instead of the smallest
            #elem_count_not_single = np.where(counts !=1)[0]
            #for i in len(elem_count_not_single):
            #  repeat_lat = np.where(self.phi_2PV == phi_2PV[elem_count_not_single[i]])[0]
            #  phi_2PV[elem_count_not_single[i]] = self.theta_2PV[repeat_lat].mean()

           
            # sort the elements to ensure the array order is the same then apply to all arrays
            phi_idx = (np.sort(phi_idx)).tolist()

        else:
            #keep all elem
            phi_idx = np.arange(0,len(self.phi_2PV), 1 ).tolist()

        #test that each 2PV point on the line is increasing in phi for NH (visa versa).
        #this is required or the min and max locals are not always identified
        i = 0
        while i <= (len(self.phi_2PV[phi_idx]) - 2):
                dphi = self.phi_2PV[phi_idx][i + 1] - self.phi_2PV[phi_idx][i]
                if hemi == 'NH' and dphi > 0:
                    # remove the element i
                    phi_idx.remove(phi_idx[i + 1])
                    i = i - 1
                if hemi == 'SH' and dphi < 0:
                    phi_idx.remove(phi_idx[i])
                    i = i - 1
                i = i + 1

        # data
        self.phi_2PV = self.phi_2PV[phi_idx]
        self.theta_2PV = self.theta_2PV[phi_idx]


        # test if there are two d_theta values that are the same next to each
        # other and if so remove one.
        theta_2PV_test1, idx_test1, idx_test2 = np.unique(
            self.theta_2PV, return_inverse=True, return_index=True)

        if len(self.theta_2PV) != len(theta_2PV_test1):
            # catch if the dimensions are different
            'Investigate this condition'
            pdb.set_trace()

    def PolyFit2PV_peaks(self, print_messages, hemi, time_loop):


        # find point where thermodynamic and 2.0 pv line (dynamic tropopause) intersect.
        # Use this crossing point as an equatorial minimum condition for where the
        # jet lat can occur.
        cross_lat, cross_lev = TropoCrossing(hemi, self.TropH_theta[time_loop, :],
                                             self.lat, self.theta_2PV, self.phi_2PV,
                                             time_loop)
 
        self.cross_lat = cross_lat
        self.cross_lev = cross_lev

        # polynomial first derivative
        x = self.phi_2PV
        y = self.dtdphi_val

        # polynomial second derivative
        x2 = self.phi_2PV
        y2 = self.d2tdphi2_val

        (elem, local_elem, local_elem_2, x_peak, y_peak, x2_peak,
         y2_peak) = IsolatePeaks(hemi, x, y, y2, time_loop, cross_lat,  print_messages)


        # using the finite dif method
        a1 = self.dTHdlat_lat
        b1 = self.dTHdlat

        (elem_finite, local_elem_finite, tmp, x_peak_finite, y_peak_finite, tmp,
         tmp) = IsolatePeaks(hemi, a1, b1, None, time_loop, cross_lat, print_messages)



        # assignment
        self.elem_cby, self.elem_fd = elem, elem_finite
        self.local_elem_cby, self.local_elem_fd = local_elem, local_elem_finite
        self.local_elem_2_cby = local_elem_2

        # assignment from poly fit difference
        self.phi_2PV_peak_cby, self.dtdphi_val_peak_cby = x_peak, y_peak
        self.phi_2PV_peak_dt2, self.dtdphi_val_peak_dt2 = x2_peak, y2_peak

        # assignment from finite difference
        self.elem_fd, self.local_elem_fd = elem_finite, local_elem_finite
        self.phi_2PV_peak_fd, self.dtdphi_val_peak_fd = x_peak_finite, y_peak_finite

        testing = False
        if testing:
            # test if peaks are different to mean
            AttemptMeanFind(hemi, time_loop, dtdphi_val, elem, phi_2PV)



    def PolyFit2PV_SortedLat(self, hemi, print_messages):
        'Sort the peaks from 0-90 in NH and -90-0 in SH'

        if hemi == 'NH':
            # cby fit
            self.sort_index_cby = np.argsort(self.phi_2PV_peak_cby)
            self.STJ_lat_sort_cby = np.sort(self.phi_2PV_peak_cby)
            # use unsorted array
            if len(self.dtdphi_val_peak_cby) != 0:
              self.peak_mag_cby = (self.dtdphi_val_peak_cby).min()

            # finite diff
            if len(self.phi_2PV_peak_fd) != 0:
              self.sort_index_fd = np.argsort(self.phi_2PV_peak_fd)
              self.STJ_lat_sort_fd = np.sort(self.phi_2PV_peak_fd)
              self.peak_mag_fd = (self.dtdphi_val_peak_fd).min()

        else:

            # cby fit
            self.sort_index_cby = np.argsort(self.phi_2PV_peak_cby)[::-1]
            self.STJ_lat_sort_cby = np.sort(self.phi_2PV_peak_cby)[::-1]
            if len(self.dtdphi_val_peak_cby) != 0.0:
              self.peak_mag_cby = (self.dtdphi_val_peak_cby).max()

            # finite diff
            self.sort_index_fd = np.argsort(self.phi_2PV_peak_fd)[::-1]
            self.STJ_lat_sort_fd = np.sort(self.phi_2PV_peak_fd)[::-1]
            if self.dtdphi_val_peak_fd.shape[0] == 0:
              self.peak_mag_fd = None 
            else:
              self.peak_mag_fd = (self.dtdphi_val_peak_fd).max()

        if print_messages:
            print('Where are peaks: cby ', self.STJ_lat_sort_cby[:])
            print('Where are peaks: f_d ', self.STJ_lat_sort_fd[:])

    def MaxShear(self, hemi, u_zonal, lat_elem, local_elem,time_loop):
        'Assign the STJ to the peak with the most shear'

        lat_hemi = self.lat[lat_elem]

        loop_len = len(self.phi_2PV[local_elem])
        shear = np.zeros(loop_len)
        shear_elem = np.zeros(loop_len)

        for i in range(loop_len):

            shear_elem[i] = FindClosestElem(self.phi_2PV[local_elem][i],
                                            self.lat[lat_elem],time_loop)[0]

            # restrict data between the surface and the level of the 2.0 PV line
            upper_lev = FindClosestElem(self.theta_2PV[local_elem][i], self.theta_lev,time_loop)[0]
            lower_lev = 0

            shear[i] = (u_zonal[:, lat_elem][upper_lev, 0, shear_elem[i]] -
                        u_zonal[:, lat_elem][lower_lev, 0, shear_elem[i]])

            test_shear = False
            if test_shear:
                plt.plot(self.lat[lat_elem][shear_elem[i]], self.theta_lev[upper_lev],
                         linestyle=' ', c='green', marker='x', markersize=10,
                         label='dTh/dy peaks')
                print('shear:', shear[i], self.phi_2PV[local_elem][i])

        #if time_loop == 439 and hemi == 'SH':
        #      pdb.set_trace()

        # Check for repeated shear_elem
        shear_elem, idx = np.unique(shear_elem, return_index=True)
        shear = shear[idx]

        shear_elem = shear_elem.tolist()

        # max shear line
        if len(shear) != 0:
          shear_max_elem = np.where(shear == shear.max())[0]
          if len(shear_max_elem) > 1:
             pdb.set_trace()

          best_guess = self.phi_2PV[local_elem][FindClosestElem(
              self.lat[lat_elem][shear_elem][shear_max_elem], self.phi_2PV[local_elem], time_loop)]
        else:
           shear_max_elem=None
           best_guess=None



        testing = False
        if testing:
            test_max_shear_across_lats(
                self.phi_2PV, self.theta_2PV, self.lat, lat_elem, self.theta_lev, u_zonal,time_loop)

        return shear_elem, shear_max_elem, best_guess

    def Trop_H_at_jet(self,hemi,lat_elem):
   
        jet_lat = self.best_guess_cby
        jet_lat_elem = np.where(self.phi_2PV ==  jet_lat)[0]
        if len(jet_lat_elem) != 0:
          self.trop_height_at_STJ = self.theta_2PV[jet_lat_elem]


    def JetIntensity(self, hemi, u_zonal, lat_elem):
        'At the best guess lat find the spline fitted u wind'

        # spline fit zonal mean onto same grid as 2PV
        function = interpolate.interp2d(self.lat[lat_elem], self.theta_lev,
                                        u_zonal[:, lat_elem][:, 0, :], kind='cubic')
        self.u_fitted = function(self.lat_hemi, self.theta_domain)

        # At the jet latitude find the maximum uwind - cby
        elem = np.where(self.best_guess_cby == self.lat_hemi)[0]
        if len(elem) != 0:
          slice_at_jet = self.u_fitted[:, elem][:, 0]
          jet_max_wind_elem = np.where(slice_at_jet == slice_at_jet.max())[0]
          self.jet_max_wind_cby = slice_at_jet[jet_max_wind_elem]
          # The level of the max wind - for plotting
          self.jet_max_theta_cby = self.theta_domain[jet_max_wind_elem]

        # fd
        if self.best_guess_fd  is not None:
          elem_fd = np.where(self.best_guess_fd == self.lat_hemi)[0]
          slice_at_jet_fd = self.u_fitted[:, elem_fd][:, 0]
          jet_max_wind_elem_fd = np.where(slice_at_jet_fd == slice_at_jet_fd.max())[0]
          self.jet_max_wind_fd = slice_at_jet_fd[jet_max_wind_elem_fd]
          self.jet_max_theta_fd = self.theta_domain[jet_max_wind_elem_fd]


    def trends_annual(self, pos,intensity,level,height,file_out_syntax):
    #null hypothesis is that the slope is zero
        hemi = ['NH','SH']
        time = (len(pos[:,0])/12)/10  #units per decade
        print 'Trends in the annual timeseries'
        for hemi_count in xrange(2):
            print hemi[hemi_count]
            slope, p_val = Get_slope(pos[:,hemi_count])
            print 'Position',slope/time , ' deg per decade with p val:',  p_val
            slope, p_val = Get_slope(intensity[:,hemi_count])
            print 'Intensity', slope/time, ' m/s per decade with p val:',  p_val
            slope, p_val = Get_slope(level[:,hemi_count])
            print 'Level', slope/time, ' K per decade with p val:',  p_val
            slope, p_val = Get_slope(height[:,hemi_count])
            print 'Height',slope/time, ' K per decade with p val:',  p_val

        save_data = True
        if save_data:
            #data for testing trends
            filename = '/home/links/pm366/Documents/Data/STJ_ts_{0}.nc'.format(file_out_syntax)
            f = io.netcdf.netcdf_file(filename, mode='w')

            f.createDimension('hemi', 2)
            hemi = f.createVariable('hemi', 'f', ('hemi',))
            hemi[0],hemi[0] = 0, 1
            hemi.string = 'NH(0), SH(1)'
  
            f.createDimension('time',  pos.shape[0])
            time = f.createVariable('time', 'f', ('time',))
            time[:] =  np.arange(0,pos.shape[0],1)
            time.range = 'Start date = Jan 1979, end is Dec 2015'

            # STJ metric
            STJ_lat = f.createVariable('STJ_lat', 'f', ('time','hemi',))
            STJ_lat[:,:] = pos[:,:]
            f.close()
            print 'Saved file: ', filename


    def trends_season(self):
        time = (len(self.STJ_seasons['DJF'][:,0])/3.0)/10.0 #seasonal change per decade time = years/10
        hemi = ['NH','SH']
        print 'Trends in the seasons'
        for hemi_count in xrange(2):
            print hemi[hemi_count]
            for season in ['DJF', 'MAM', 'JJA', 'SON']:
                print season
                slope, p_val = Get_slope(self.STJ_seasons[season][:,hemi_count])
                print 'Position',slope/time , ' deg seasonal change per decade with p val:',  p_val
                slope, p_val = Get_slope(self.STJ_I_seasons[season][:,hemi_count])
                print 'Intensity', slope/time, ' m/s seasonal change per decade with p val:',  p_val
                slope, p_val = Get_slope(self.STJ_th_seasons[season][:,hemi_count])
                print 'Level', slope/time, ' K seasonal change per decade with p val:',  p_val
                slope, p_val = Get_slope(self.H_th_seasons[season][:,hemi_count])
                print 'Height',slope/time, ' K seasonal change per decade with p val:',  p_val

    def AnnualCorrelations(self, best_guess_cby, jet_H_lev, jet_max_wind_cby, jet_max_theta_cby,
                           crossing, group):

        var_name, num_var, hemi = plot_group_info(group)
        data = np.zeros([best_guess_cby.shape[0], num_var, 2])
        data[:, 0, :] = best_guess_cby
        data[:, 1, :] = jet_max_wind_cby
        data[:, 2, :] = jet_max_theta_cby
        if group == 'h':
          data[:, 3, :] = jet_H_lev
        if group == 'x':
          data[:, 3, :] = crossing


        self.AnnualCC = GetCorrelation(hemi, num_var, var_name, data)
        print 'Correlations:' 
        print 'NH:', self.AnnualCC[:,:,0]
        print 'SH:', self.AnnualCC[:,:,1]

        # Now get the partial correlation
        pc = np.zeros([num_var, num_var, 2])
        pc[:, :, 0] = partial_corr(data[:, :, 0])
        pc[:, :, 1] = partial_corr(data[:, :, 1])

        self.AnnualPC = pc
        print 'Partial Correlations all four metrics: '
        print 'NH:', pc[:,:,0]
        print 'SH:', pc[:,:,1]

        print 'Partial Correlations only three metrics: '
        var_name, num_var, hemi = plot_group_info('core')
        pc_3_var = np.zeros([num_var, num_var, 2])
        pc_3_var[:, :, 0] = partial_corr(data[:, 0:3, 0])
        pc_3_var[:, :, 1] = partial_corr(data[:, 0:3, 1])
        print 'NH:', pc_3_var[:,:,0]
        print 'SH:', pc_3_var[:,:,1]
        self.AnnualPC_3_var = pc_3_var

    def MonthlyCorrelations(self, best_guess_cby, jet_H_lev, jet_max_wind_cby,
                            jet_max_theta_cby, crossing, group):

        var_name, num_var, hemi = plot_group_info(group)

        best_guess_cby = best_guess_cby.reshape([self.yy, 12, 2])
        jet_max_wind_cby = jet_max_wind_cby.reshape([self.yy, 12, 2])
        jet_max_theta_cby = jet_max_theta_cby.reshape([self.yy, 12, 2])
        if group == 'h':
          jet_H_lev = jet_H_lev.reshape([self.yy, 12, 2])
        if group == 'x':
          cross = crossing.reshape([self.yy, 12, 2])

        data = np.zeros([best_guess_cby.shape[0], num_var, 2])
        corr = np.zeros([best_guess_cby.shape[1], num_var, num_var, 2])
        pc = np.zeros([best_guess_cby.shape[1], num_var, num_var, 2])

        for mm in range(12):

            data[:, 0, :] = best_guess_cby[:, mm, :]
            data[:, 1, :] = jet_max_wind_cby[:, mm, :]
            data[:, 2, :] = jet_max_theta_cby[:, mm, :]
            if group == 'h':
                data[:, 3, :] = jet_H_lev[:, mm, :]
            if group == 'x':
                data[:, 3, :] = cross[:, mm, :]

                corr[mm, :, :, :] = GetCorrelation(hemi, num_var, var_name, data)

            # Now get the partial correlation

            pc[mm, :, :, 0] = partial_corr(data[:, :, 0])
            pc[mm, :, :, 1] = partial_corr(data[:, :, 1])

        self.MonthlyCC = corr
        self.MonthlyPC = pc

    def SeasonCorrelations(self,group):

        var_name, num_var, hemi = plot_group_info(group)
        season_names = ['DJF', 'MAM', 'JJA', 'SON']

        data = np.zeros([self.STJ_seasons['DJF'].shape[0],
                         num_var, 2])  # [months, var, hemi]
        pc_store = np.zeros([num_var, num_var, 2])

        corr = {}
        pc = {}
        for season_count in range(4):

            data[:, 0, :] = self.STJ_seasons[season_names[season_count]]
            data[:, 1, :] = self.STJ_I_seasons[season_names[season_count]]
            data[:, 2, :] = self.STJ_th_seasons[season_names[season_count]]
            if group == 'h':
                data[:, 3, :] = self.H_th_seasons[season_names[season_count]]
            if group == 'x':
                data[:, 3, :] = self.cross_seasons[season_names[season_count]]

            corr[season_names[season_count]] = GetCorrelation(
                hemi, num_var, var_name, data)

            # Now get the partial correlation
            pc_store[:, :, 0] = partial_corr(data[:, :, 0])
            pc_store[:, :, 1] = partial_corr(data[:, :, 1])
            pc[season_names[season_count]] = copy.deepcopy(pc_store)
 
        self.SeasonCC = corr
        self.SeasonPC = pc
        self.var_name = var_name

    def PolyFit2PV_near_mean(self, print_messages, STJ_mean, EDJ_mean, phi_val,
                             local_elem, STJ_lat_sort, y_peak, peak_mag, sort_index,time_loop):
        """
        Find the peaks that are closest to known mean position.
        Tests if ordered or ID peaks different.
        """

        local_lat = phi_val.tolist()
        local_elem_cp = copy.deepcopy(local_elem)
        # find the peaks closest to know location

        if len(local_lat) == 0:
          STJ_lat_near_mean = None
        else:
          STJ_elem = FindClosestElem(STJ_mean, np.array(local_lat),time_loop)
          STJ_lat_near_mean = local_lat[STJ_elem]

        if len(local_lat) >= 2:
            # don't identify the same element so remove it from the sample
            local_elem_cp.remove(local_elem_cp[STJ_elem])
            local_lat.remove(local_lat[STJ_elem])

            EDJ_elem = FindClosestElem(EDJ_mean, np.array(local_lat),time_loop)
            EDJ_lat_near_mean = local_lat[EDJ_elem]

            local_elem_cp.remove(local_elem_cp[EDJ_elem])
            local_lat.remove(local_lat[EDJ_elem])

            additional_lat = local_lat

        else:

            EDJ_lat_near_mean, additional_lat = None, None

        if print_messages:
            print('Testing near mean position: ', STJ_lat_near_mean, ',      EDJ: ',
                  EDJ_lat_near_mean, ',        Other: ', additional_lat)

        if print_messages:
            if STJ_lat_sort[0] != STJ_lat_near_mean:
                print('STJ_lat_sort[0] != STJ_lat', STJ_lat_sort[0], STJ_lat_near_mean)

        # peaks = y_peak
        # peak_elem = np.where(peaks == peak_mag)[0]
        # peak_lat = self.phi_2PV.tolist()[peak_elem]
        # peak_lat_sort = peaks[sort_index]

        return STJ_lat_near_mean, EDJ_lat_near_mean, additional_lat

    def SaveDataForTesting(self):

        filename = '/home/links/pm366/Documents/Data/STJ_IPV_output.nc'
        f = io.netcdf.netcdf_file(filename, mode='w')

        # STJ metric
        f.createDimension('STJ_lat', len(self.best_guess_cby))
        STJ_lat = f.createVariable('STJ_lat', 'f', ('STJ_lat',))
        STJ_lat[:] = self.best_guess_cby

        # d theta / d phi
        f.createDimension('dthdlat', len(self.dtdphi_val))
        dthdlat = f.createVariable('dthdlat', 'f', ('dthdlat',))
        dthdlat[:] = self.dtdphi_val

        # lat of 2PV line
        f.createDimension('lat_2PV_line', len(self.phi_2PV))
        lat_2PV_line = f.createVariable('lat_2PV_line', 'f', ('lat_2PV_line',))
        lat_2PV_line[:] = self.phi_2PV

        # theta of 2PV line
        f.createDimension('theta_2PV_line', len(self.theta_2PV))
        theta_2PV_line = f.createVariable('theta_2PV_line', 'f', ('theta_2PV_line',))
        theta_2PV_line[:] = self.theta_2PV

        f.close()

    def SaveOutput(self, STJ, cross, intens, theta,file_out_syntax):

        filename = '{0}/STJ_data_{1}.nc'.format(data_out_dir,file_out_syntax)
        f = io.netcdf.netcdf_file(filename, mode='w')

        f.createDimension('hemi', 2)
        hemi = f.createVariable('hemi', 'f', ('hemi',))
        hemi[0],hemi[0] = 0, 1
        hemi.string = 'NH(0), SH(1)'
  
        f.createDimension('time', STJ.shape[0])
        time = f.createVariable('time', 'f', ('time',))
        time[:] =  np.arange(0,STJ.shape[0],1)
        time.range = 'Start date = Jan 1979, end is Dec 2015'

        # STJ metric
        STJ_lat = f.createVariable('STJ_lat', 'f', ('time','hemi',))
        STJ_lat[:,:] = STJ[:,:]

        # STJ metric
        STJ_int = f.createVariable('STJ_int', 'f', ('time','hemi',))
        STJ_int[:,:] = intens[:,:]

        # STJ metric
        STJ_lev = f.createVariable('STJ_lev', 'f', ('time','hemi',))
        STJ_lev[:,:] = theta[:,:]

        # crossing
        cross_lat = f.createVariable('cross_lat', 'f', ('time','hemi',))
        cross_lat[:,:] = cross[:,:]

        f.close()
        print 'Created file: ', filename
        pdb.set_trace()

    def SeasonalPeaks(self, seasons, STJ_array, crossing_lat, STJ_I, STJ_th,jet_H_lev,file_out_syntax):

        # this is an overkill way to treat the seasons but has reduced risk of
        # mixing them up

        STJ_seasons = np.zeros([STJ_array.shape[0] / 4, STJ_array.shape[1], 4])
        cross_seasons = np.zeros([crossing_lat.shape[0] / 4, crossing_lat.shape[1], 4])
        STJ_I_seasons = np.zeros([crossing_lat.shape[0] / 4, crossing_lat.shape[1], 4])
        STJ_th_seasons = np.zeros([crossing_lat.shape[0] / 4, crossing_lat.shape[1], 4])
        STJ_H_lev_seasons = np.zeros([crossing_lat.shape[0] / 4, crossing_lat.shape[1], 4])

        STJ_DJF =  np.zeros([STJ_array.shape[0], STJ_array.shape[1]])
        STJ_MAM =  np.zeros([STJ_array.shape[0], STJ_array.shape[1]])
        STJ_JJA =  np.zeros([STJ_array.shape[0], STJ_array.shape[1]])
        STJ_SON =  np.zeros([STJ_array.shape[0], STJ_array.shape[1]])

        count_DJF, count_MAM, count_JJA, count_SON = 0, 0, 0, 0

        STJ_DJF[:,:],STJ_MAM[:,:],STJ_JJA[:,:],STJ_SON[:,:]  = np.nan,np.nan,np.nan,np.nan 


        for i in range(STJ_array.shape[0]):
            if seasons[i] == 'DJF':
                STJ_seasons[count_DJF, :, 0] = STJ_array[i, :]
                cross_seasons[count_DJF, :, 0] = crossing_lat[i, :]
                STJ_I_seasons[count_DJF, :, 0] = STJ_I[i, :]
                STJ_th_seasons[count_DJF, :, 0] = STJ_th[i, :]
                STJ_H_lev_seasons[count_DJF, :, 0] = jet_H_lev[i, :]
                STJ_DJF[i,:] = STJ_array[i, :]
                count_DJF = count_DJF + 1

            if seasons[i] == 'MAM':
                STJ_seasons[count_MAM, :, 1] = STJ_array[i, :]
                cross_seasons[count_MAM, :, 1] = crossing_lat[i, :]
                STJ_I_seasons[count_MAM, :, 1] = STJ_I[i, :]
                STJ_th_seasons[count_MAM, :, 1] = STJ_th[i, :]
                STJ_H_lev_seasons[count_MAM, :, 1] = jet_H_lev[i, :]
                STJ_MAM[i,:] = STJ_array[i, :]
                count_MAM = count_MAM + 1

            if seasons[i] == 'JJA':
                STJ_seasons[count_JJA, :, 2] = STJ_array[i, :]
                cross_seasons[count_JJA, :, 2] = crossing_lat[i, :]
                STJ_I_seasons[count_JJA, :, 2] = STJ_I[i, :]
                STJ_th_seasons[count_JJA, :, 2] = STJ_th[i, :]
                STJ_H_lev_seasons[count_JJA, :, 2] = jet_H_lev[i, :]
                STJ_JJA[i,:] = STJ_array[i, :]
                count_JJA = count_JJA + 1

            if seasons[i] == 'SON':
                STJ_seasons[count_SON, :, 3] = STJ_array[i, :]
                cross_seasons[count_SON, :, 3] = crossing_lat[i, :]
                STJ_I_seasons[count_SON, :, 3] = STJ_I[i, :]
                STJ_th_seasons[count_SON, :, 3] = STJ_th[i, :]
                STJ_H_lev_seasons[count_SON, :, 3] = jet_H_lev[i, :]
                STJ_SON[i,:] = STJ_array[i, :]
                count_SON = count_SON + 1

        output = {}
        output['DJF'] = STJ_seasons[:, :, 0]  # month, hemi, season
        output['MAM'] = STJ_seasons[:, :, 1]
        output['JJA'] = STJ_seasons[:, :, 2]
        output['SON'] = STJ_seasons[:, :, 3]

        cross = {}
        cross['DJF'] = cross_seasons[:, :, 0]  # month, hemi, season
        cross['MAM'] = cross_seasons[:, :, 1]
        cross['JJA'] = cross_seasons[:, :, 2]
        cross['SON'] = cross_seasons[:, :, 3]

        intensity = {}
        intensity['DJF'] = STJ_I_seasons[:, :, 0]  # month, hemi, season
        intensity['MAM'] = STJ_I_seasons[:, :, 1]
        intensity['JJA'] = STJ_I_seasons[:, :, 2]
        intensity['SON'] = STJ_I_seasons[:, :, 3]

        theta = {}
        theta['DJF'] = STJ_th_seasons[:, :, 0]  # month, hemi, season
        theta['MAM'] = STJ_th_seasons[:, :, 1]
        theta['JJA'] = STJ_th_seasons[:, :, 2]
        theta['SON'] = STJ_th_seasons[:, :, 3]

        STJ_year = {}
        STJ_year['DJF'] = STJ_DJF
        STJ_year['MAM'] = STJ_MAM
        STJ_year['JJA'] = STJ_JJA
        STJ_year['SON'] = STJ_SON

        H_th = {}
        H_th['DJF'] = STJ_H_lev_seasons[:, :, 0]  # month, hemi, season
        H_th['MAM'] = STJ_H_lev_seasons[:, :, 1]
        H_th['JJA'] = STJ_H_lev_seasons[:, :, 2]
        H_th['SON'] = STJ_H_lev_seasons[:, :, 3]

        self.STJ_seasons = output
        self.cross_seasons = cross
        self.STJ_I_seasons = intensity
        self.STJ_th_seasons = theta
        self.STJ_year = STJ_year
        self.H_th_seasons = H_th


        make_ts_seasonal_plot = True
        if make_ts_seasonal_plot:
            print_min_max_mean(output)
            plot_seasonal_stj_ts(output, cross, STJ_year,file_out_syntax)

    def CalendarMean(self, seasons, STJ_array, crossing_lat, STJ_I, STJ_th,jet_H_lev,group,file_out_syntax):

        STJ_cal = STJ_array.reshape([self.yy, 12, 2])
        STJ_cal_mean = MeanOverDim(data=STJ_cal, dim=0)

        STJ_cal_int = STJ_I.reshape([self.yy, 12, 2])
        STJ_cal_int_mean = MeanOverDim(data=STJ_cal_int, dim=0)

        STJ_cal_th = STJ_th.reshape([self.yy, 12, 2])
        STJ_cal_th_mean = MeanOverDim(data=STJ_cal_th, dim=0)

        STJ_cal_x = crossing_lat.reshape([self.yy, 12, 2])
        STJ_cal_x_mean = MeanOverDim(data=STJ_cal_x, dim=0)

        STJ_cal_H = jet_H_lev.reshape([self.yy, 12, 2])
        STJ_cal_H_mean = MeanOverDim(data=STJ_cal_H, dim=0)



        mean_val = {}
        for season in ['DJF', 'MAM', 'JJA', 'SON']:
            mean_val[season, 'lat'] = MeanOverDim(data=self.STJ_seasons[season], dim=0)
            mean_val[season, 'I'] = MeanOverDim(data=self.STJ_I_seasons[season], dim=0)
            mean_val[season, 'th'] = MeanOverDim(data=self.STJ_th_seasons[season], dim=0)
            if group == 'x':
                mean_val[season, 'x'] = MeanOverDim(data=self.cross_seasons[season], dim=0)
                var_4 = STJ_cal_x_mean
            if group == 'h':
                mean_val[season, 'h'] = MeanOverDim(data=self.H_th_seasons[season], dim=0)
                var_4 = STJ_cal_H_mean

        if group == 'core':
          var_4 = None
          print 'Need to change plot for none as input'
        else:
            PlotCalendarTimeseries(STJ_cal_mean, STJ_cal_int_mean,
                               STJ_cal_th_mean, var_4, self.AnnualPC_3_var,mean_val, self.AnnualPC,group,file_out_syntax)

    def validate_near_mean(self, hemi_count, season, input_string, hemi, time_loop):

        # test each month to see if the STJ lat is anywhere near the mean.
        # if it is not near it then print it.

        mean_val = self.STJ_seasons[season][:, hemi_count].mean()

        if np.abs(self.STJ_lat_near_mean) - np.abs(mean_val) >= np.abs(5.0):
            print('Seasonal mean is ', mean_val, ' but current jet lat is ',
                  self.STJ_lat_near_mean, 'for ', input_string)
            PlottingObject = Plotting(Method)
            best_guess_jet = PlottingObject.poly_2PV_line(hemi, time_loop, pause=False)


        # keep_peak = np.zeros(len(Method.phi_2PV_peak)-1)
        # for i in xrange(len(Method.phi_2PV_peak)-1):
        #     if Method.phi_2PV_peak[i+1] - Method.phi_2PV_peak[i] > np.abs(15.0):
        #         pdb.set_trace()
        #     keep_peak[i] =

    def TropopauseTheta(self):

        # Identidy the theta level where the thermodynamic tropopause is

        Rd = 287.0
        Cp = 1004.0
        kappa = Rd / Cp

        self.TropH_theta = self.TropH_temp * (1000.0 / self.TropH_p) ** kappa

    def PlotTesting(self, time_loop):

        path = self.diri.data_loc + 'Data/ERA_INT/'
        u_fname = path + 'ERA_INT_UWind_correct_levels.nc'
        # u_fname = '/home/pm366/Documents/Data/tmp/ERA_INT_UWind_correct_levels.nc'

        var = openNetCDF4_get_data(u_fname)

        uwind168_surf = var['var131'][time_loop, -1, :, :]
        uwind168_top = var['var131'][time_loop, 11, :, :]
        uwind168_del = (var['var131'][time_loop, 11, :, :] -
                        var['var131'][time_loop, -1, :, :])

        filename_surf = '{}/uwind_{}_1000.eps'.format(self.diri.plot_loc, time_loop)
        filename_top = '{}/uwind_{}_250.eps'.format(self.diri.plot_loc, time_loop)
        filename_del = '{}/uwind_{}_del.eps'.format(self.diri.plot_loc, time_loop)

        plot = draw_map_model(uwind168_surf, var['lon'], var['lat'], 'Surface (1000)',
                              'cbar', 'RdBu_r', np.arange(-30, 30, 5.0), filename_surf,
                              False, coastline=True)
        plot = draw_map_model(uwind168_top, var['lon'], var['lat'], '250', 'cbar',
                              'RdBu_r', np.arange(-60, 60, 5.0), filename_top, False,
                              coastline=True)
        plot = draw_map_model(uwind168_del, var['lon'], var['lat'], '250-1000', 'cbar',
                              'RdBu_r', np.arange(-60, 60, 5.0), filename_del, False,
                              coastline=True)

        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])

        ax.plot(self.phi_2PV, self.dtdphi_val, linestyle='-', c='r',
                marker='.', markersize=8, label='dTh/dy from fit')
        ax.plot(self.phi_2PV, self.d2tdphi2_val, linestyle='-', c='b',
                marker='.', markersize=8, label='d2Th/dy2 from fit')
        ax.plot(self.phi_2PV_peak_dt2, self.dtdphi_val_peak_dt2, linestyle=' ',
                c='b', marker='x', markersize=10, label='d2Th/dy2 peaks')
        ax.plot(self.phi_2PV_peak, self.dtdphi_val_peak, linestyle=' ',
                c='r', marker='x', markersize=10, label='dTh/dy peaks')
        ax.set_ylim(-5, 5)
        plt.legend(loc=0)

        ax2 = ax.twinx()
        ax2.plot(self.phi_2PV, self.theta_2PV / 100., linestyle='-', c='k',
                 marker='x', markersize=8, label='2PV line scaled by x1/100')
        ax2.set_ylim(3, 4)

        plt.legend(loc=0)
        plt.savefig('{}/test_second_der{}.eps'.format(self.diri.plot_loc, time_loop))
        #plt.show()

    def PV_differentiate(self):
        'Finite difference method to calculate the change in derivative in PV'

        # Differentiate to find steepest local maximum of a near-2PV elements
        non_zero_len = len(self.theta_2PV)
        dTHdlat = np.zeros(non_zero_len - 1)
        dTHdlat_lat = np.zeros(non_zero_len - 1)  # latitude for phi between points

        for PV_line_loop in range(non_zero_len - 1):

            dTH = self.theta_2PV[PV_line_loop + 1] - self.theta_2PV[PV_line_loop]
            dlat = self.phi_2PV[PV_line_loop + 1] - self.phi_2PV[PV_line_loop]

            if dlat == 0.0:
                # when multiple theta have the same lat, add a small increment so that
                # derivative is not inf.
                dlat = 0.01

            dTHdlat[PV_line_loop] = np.abs(dTH / dlat)
            dTHdlat_lat[PV_line_loop] = ((self.phi_2PV[PV_line_loop + 1] -
                                          self.phi_2PV[PV_line_loop]) /
                                         2. + self.phi_2PV[PV_line_loop])

        plot_finite_diff = False
        if plot_finite_diff:
            test_finite_difference(phi_2PV, theta_2PV, dTHdlat_lat, dTHdlat)

        # note this is no longer self.phi_2PV as it is now centred on a grid
        # centre not edge
        self.dTHdlat_lat = dTHdlat_lat
        self.dTHdlat = dTHdlat

    def test_method_plot(self, time_loop, u_th, ipv_interp, lat, ipv_zonal, phi_2PV,
                         theta_2PV, dThdlat_lat, dThdlat_theta, phi_2PV_max,
                         theta_2PV_max):
        'Investigate if STJ lat is identified in tropics or extratropics'
        u_plot = MeanOverDim(data=u_th[time_loop, :, :, :], dim=2)
        array_shape = u_plot.shape
        lat_array = self.lat[np.newaxis, :] + np.zeros(array_shape)
        theta_lev_array = get_ipv.th_levels_trop[:, np.newaxis] + np.zeros(array_shape)

        array_shape_interp = ipv_interp.shape
        lat_array_interp = lat[np.newaxis, :] + np.zeros(array_shape_interp)
        # theta_array_interp  = (self.theta_interp[:, np.newaxis] +
        #                        np.zeros(array_shape_interp))

        filename = '{}/IPV_uwind_contour_test_cases.eps'.format(self.diri.plot_loc)
        fig = plt.figure(figsize=(8, 8))
        ax1 = fig.add_axes([0.1, 0.2, 0.75, 0.75])
        cmap = plt.cm.RdBu_r
        bounds = np.arange(-50, 51, 5.0)
        norm = mpl.colors.BoundaryNorm(bounds, cmap.N)
        ax1.pcolormesh(lat_array, theta_lev_array, u_plot, cmap=cmap, norm=norm)
        cs1 = ax1.contour(lat_array, theta_lev_array, ipv_zonal,
                          levels=np.arange(2, 3, 1), colors='red')
        cs2 = ax1.contour(lat_array, theta_lev_array, ipv_zonal,
                          levels=np.arange(-2, -1, 1), colors='red')
        plt.plot(phi_2PV, theta_2PV, marker='x', c='k', linestyle=' ', markersize=10)
        plt.plot(dThdlat_lat[time_loop], dThdlat_theta[time_loop],
                 marker='x', c='red', linestyle=' ', markersize=10)
        # plt.plot(dThdlat_lat[local_maxima], dThdlat_theta[local_maxima],  marker='x',
        #          c='blue', linestyle=' ', markersize = 10)
        # plt.plot(dThdlat_lat[local_minima], dThdlat_theta[local_minima],  marker='x',
        #          c='green', linestyle=' ', markersize = 10)

        plt.xticks(np.arange(-90.0, 91.0, 15))
        plt.ylabel('Potential Temperature')
        plt.title('U wind. 2PV contour (red). 0th month. X close 2PV (Red max der).')
        plt.ylim(300, 400)
        plt.savefig(filename)
        #plt.show()
        pdb.set_trace()



def Get_slope(timeseries):
    time = np.arange(0,len(timeseries))
    slope, intercept, r_value, p_value, std_err = linregress(timeseries, time)
    #beta_i = linalg.lstsq(pos[:,0], time)[0]
    #pdb.set_trace()

    #if p<=0.05 then reject the null.
    if p_value > 0.05: 
      slope, p_value = 0.0,p_value

    return slope, p_value

def IPV_get_2PV(data, pv_line,time_loop):

    array_len = data.shape[0]
    line_2PV_elem = np.zeros(array_len)
    actual_ipv_values = np.zeros(array_len)

    for i in range(array_len):
        data_loc = data[i, :]
        elem = FindClosestElem(pv_line, data_loc, time_loop)[0]
        line_2PV_elem[i] = elem
        actual_ipv_values[i] = data[i, elem]

    return line_2PV_elem, actual_ipv_values


def func(x, a, b, c):
    return a * np.exp(-b * x) + c


def TropoCrossing(hemi, H, H_lat, theta_IPV, phi_IPV, time_loop):
    'find the intersection of 2.0PV line (dynamic tropopause) and thermal tropopause'

    # assign latitute element location and the thermal tropopause height (H)
    if hemi == 'NH':
        elem = np.where(H_lat > 0.0)[0]
    else:
        elem = np.where(H_lat < 0.0)[0]

    H_hemi = H[elem]
    H_lat_hemi = H_lat[elem]

    # Interpolate the thermal tropopause height onto the same grid as the
    # dynamics tropopause height
    if hemi == 'NH':
        min_range = max([phi_IPV.min(), H_lat_hemi.min()])
        max_range = min([phi_IPV.max(), H_lat_hemi.max()])
        new_lat = np.arange(max_range, min_range, -1)
    else:
        min_range = max([phi_IPV.min(), H_lat_hemi.min()])
        max_range = min([phi_IPV.max(), H_lat_hemi.max()])
        new_lat = np.arange(min_range, max_range, 1)

    # linear interpolate tropopause height and 2 IPV line to every 1 deg
    spline_function = interpolate.interp1d(
        H_lat_hemi, H_hemi, kind='linear')  # the spline fit did not work well
    h_spline_fit = spline_function(new_lat)

    spline_function2 = interpolate.interp1d(phi_IPV, theta_IPV, kind='linear')
    IPV_spline_fit = spline_function2(new_lat)

    # subtract the two tropopause definitions and the minimum is where they cross
    #diff = np.abs(h_spline_fit - IPV_spline_fit)
    #loc = np.where(diff == diff.min())[0]
    #cross_lat = new_lat[loc]
    #cross_lev = IPV_spline_fit[loc]

    #Find the 2PV line data that is higher than thermal definition
    dyn_gt_thermal = np.where(IPV_spline_fit> h_spline_fit)[0] 
    if len(dyn_gt_thermal) == 0:
        #at no point is the dynamic larger than thermal. In this case take the smallest lat
        cross_lat =  new_lat[-1]
        cross_lev = IPV_spline_fit[-1]
        print 'zero crossing time loop issue', time_loop


    else:
      if hemi == 'SH':
        x_elem = np.where(new_lat == new_lat[dyn_gt_thermal].min())[0]
        cross_lat =  new_lat[x_elem - 1]
        cross_lev = IPV_spline_fit[x_elem - 1]
      if hemi == 'NH':
        x_elem = np.where(new_lat == new_lat[dyn_gt_thermal].max())[0]
        cross_lat =  new_lat[x_elem - 1]
        cross_lev = IPV_spline_fit[x_elem - 1]

    #from equator to pole, find the first point where the thermal tropopause is larger than dynamic
    #lat_sort_elem = new_lat.argsort()
    #dyn_gt_thermal = np.where(IPV_spline_fit> h_spline_fit)[0] 
    #h_spline_fit[lat_sort_elem] 
    #IPV_spline_fit[lat_sort_elem]



    testing = False
    if testing:
        test_crossing_lat(H_lat_hemi, H_hemi, new_lat, h_spline_fit,
                          phi_IPV, theta_IPV, IPV_spline_fit, cross_lat)

    return cross_lat, cross_lev

def plot_group_info(group):
    if group == 'core':
      var_name = ['lat', 'int', 'lev']
    if group == 'x':
      var_name = ['lat', 'int', 'lev', 'x']
    if group == 'h':
      var_name = ['lat', 'int', 'lev', 'h']
    num_var = len(var_name)
    hemi = ['NH', 'SH']
 
    return var_name, num_var, hemi


def IsolatePeaks(hemi, x, y, y2, time_loop, cross_lat, print_messages):
    """ Find the peaks in the two derivatives of the 2.0 Pv line.
            Input data lat of the form 90-0 N and -90-0S.
            Peaks can not be first or last two points.
    """



    #Find the peaks first. Then restrict between zero crossing and the pole


    # for peak finding allow the element before the crossing point to be
    # included in case its a peak
    cross_lat_elem = FindClosestElem(cross_lat, x,time_loop)
    #if there is more than one lat that is the cross_lat_elem then keep the smaller latitude
    if cross_lat_elem.shape[0] > 1:
        cross_lat_elem = cross_lat_elem.max()  #closest to equator in both hemi

    #Near the max gradient there are a lot of points which can make it difficult for argrelmin
    # to attribute the local max and min.

    #elem = np.arange(len(x)).tolist()

    #list_elem = [0, cross_lat_elem + 2]
    #x = x[list_elem[0]:list_elem[1]]
    #y = y[list_elem[0]:list_elem[1]]
    #elem = elem[list_elem[0]:list_elem[1]]

    # isolate data poleward of the crossing latitude and equatorward of the
    # upper limit (currently 90 deg)
    if hemi == 'NH':
            # do not keep first or last two points. Magnitude of derivative oftern large
            # elem = elem[2:-2]
            # find the local maxima in NH
        #local_elem = np.array(elem)[(argrelmin(y)[0])].tolist()
        local_elem = argrelmin(y)[0].tolist()

        if y2 is not None:  # find the second derivative local peaks
            #y2 = y2[list_elem[0]:list_elem[1]]
            #local_elem_2 = np.array(elem)[(argrelmin(y2)[0])].tolist()
            local_elem_2 = argrelmin(y2)[0].tolist()
    else:
        # do not keep first or last two points. Magnitude of derivative oftern large
        # elem = elem[2:-2]
        # find the local minima in SH (due to -ve lat)
        #local_elem = np.array(elem)[(argrelmax(y)[0])].tolist()
        local_elem = argrelmax(y)[0].tolist()

        if y2 is not None:
            #y2 = y2[list_elem[0]:list_elem[1]]
            #local_elem_2 = np.array(elem)[(argrelmax(y2)[0])].tolist()
            local_elem_2 = argrelmax(y2)[0].tolist()



    # assign the local maxima
  #  x_peak, y_peak = x[local_elem], y[local_elem]
    #restrict to poleward side of tropopause crossing
    cross_lat = x[cross_lat_elem]
    if hemi == 'NH':
      value_peaks =  np.where(x[local_elem]>=cross_lat )[0]
    if hemi == 'SH':
      value_peaks =  np.where(x[local_elem]<=cross_lat )[0]

    x_peak = x[local_elem][value_peaks]
    y_peak = y[local_elem][value_peaks]
    #the valid lat range
    elem = np.arange(len(x)).tolist()
    list_elem = [0, cross_lat_elem + 2]
    elem = elem[list_elem[0]:list_elem[1]]

    if y2 is not None:
        #x2_peak, y2_peak = x[local_elem_2], y2[local_elem_2]
        if hemi == 'NH':
          value_peaks =  np.where(x[local_elem_2]>=cross_lat )[0]
        if hemi == 'SH':
          value_peaks =  np.where(x[local_elem_2]<=cross_lat )[0]
        x2_peak = x[local_elem_2][value_peaks]
        y2_peak = y[local_elem_2][value_peaks]
    else:
        x2_peak, y2_peak, local_elem_2 = None, None, None

    if print_messages:
        print('-----------------------------')
        print(hemi, ' peak is', x_peak)
        print(hemi, ' elements', x[elem])
        print('-----------------------------')

        if len(x_peak) == 0:
            print('No peak found.')
            pdb.set_trace()

    return elem, local_elem, local_elem_2, x_peak, y_peak, x2_peak, y2_peak


def season_mask(time_len):
    'Assign a season to each month of data'

    mask = np.zeros(time_len)
    mask_number = np.zeros(time_len)
    time_tmp = np.arange(0, time_len, 1)
    mask = []
    for i in time_tmp:
        year = i // 12
        month = i - year * 12
        if month == 0 or month == 1 or month == 11:
            mask.append('DJF')
            mask_number[i] = 0
        if month >= 2 and month <= 4:
            mask.append('MAM')
            mask_number[i] = 1
        if month >= 5 and month <= 7:
            mask.append('JJA')
            mask_number[i] = 2
        if month >= 8 and month <= 10:
            mask.append('SON')
            mask_number[i] = 3

    return mask, mask_number


def MakeOutfileSavez_grid(filename, lat, theta, u, h):
    'save data for testing purposed only. Works if testing is done in python'

    np.savez(filename, lat=lat, theta=theta, u_zonal=u, H_thermal=h)
    print('File created: ', filename)

    # test it opens
    npzfile = np.load(filename)
    npzfile.files
    pdb.set_trace()


def MakeOutfileSavez_derived(filename, phi_2PV, theta_2PV, dth, dth_lat, d2th):

    # save data for Mike to test
    np.savez(filename, lat=phi_2PV, theta=theta_2PV, dth=dth, dth_lat=dth_lat, d2th=d2th)
    print('File created: ', filename)
    # test it opens
    npzfile = np.load(filename)
    npzfile.files

    pdb.set_trace()

def T_to_theta_for_plotting(data_loc):

    path = "{}{}".format(data_loc, 'Data/ERA_INT/1979_2015/')
    t_fname = "{}{}".format(path, 't79_15.nc')

    var = openNetCDF4_get_data(t_fname)

    T_zonal =  MeanOverDim(data=var['t'], dim=3)

    P_spline = np.arange(10, 1001, 10)
    T_spline = np.zeros([T_zonal.shape[0],len(P_spline) ,T_zonal.shape[2]])

    for time in range(T_zonal.shape[0]):
        for lat in range(T_zonal.shape[2]):
           # use hPa instead of Pa
           tck = interpolate.splrep(var['lev'], T_zonal[time, :, lat])
           T_spline[time,:,lat] = interpolate.splev(P_spline, tck, der=0)

    theta_val = theta(T_spline,P_spline*100)

    return theta_val,  P_spline

def calc_metric(IPV_data, diri, u_fname,data_loc,test_daily,label_p):
    'Input assumed to be a dictionary'

    output_plotting = {}

    # Define the object and init the variables of interest
    Method = Method_2PV_STJ(IPV_data, diri,u_fname)

    # Manage arrays for interpolation
    Method.PrepForAlgorithm()

    # Get theta level of thermal tropopause height
    Method.TropopauseTheta()
 
    plot_trop = True
    if plot_trop:
        theta_zonal, pres  = T_to_theta_for_plotting(data_loc)
        plot_thermap_trop_vs_pressure(IPV_data,Method.TropH_theta, theta_zonal,pres)


    # Flag to assign if using zonal or run code along multiple longitude slices.
    # Default is zonal mean. Lon slices untested
    slide_method_opt = ['zonal_mean', 'lon_slices']
    slide_method = slide_method_opt[0]

    if slide_method == 'lon_slices':
        # For each month, take longitude slices to find the max slope in 2.0 IPV
        Method.Prep_lon_slices()
    else:
        # just to make loops work once (needed for compatability with lon slice method)
        Method.lon_loc = [0.0]
        lon_elem = None

    # Assign storage arrays

    # Hemispherically separate to produce jet metrics for each hemisphere
    # [time, hemi, theta in restriced domain]
    phi_2PV_out = np.zeros([Method.IPV.shape[0], 2, Method.theta_domain.shape[0]])
    theta_2PV_out = np.zeros([Method.IPV.shape[0], 2, Method.theta_domain.shape[0]])
    dth_out = np.zeros([Method.IPV.shape[0], 2, Method.theta_domain.shape[0]])
    d2th_out = np.zeros([Method.IPV.shape[0], 2, Method.theta_domain.shape[0]])
    dth_lat_out = np.zeros([Method.IPV.shape[0], 2, Method.theta_domain.shape[0]])

    # STJ metric [time_loop, lon_loop, hemi_count, cby or fd]
    jet_best_guess = np.zeros([Method.IPV.shape[0], len(Method.lon_loc), 2, 2])
    mask_jet_number = np.zeros([Method.IPV.shape[0], 2])  # how many peaks were found
    # point where two tropopause definitions cross paths
    crossing_lat = np.zeros([Method.IPV.shape[0], 2])
    # [time_loop, lon_loop, hemi_count, cby or fd]
    jet_intensity = np.zeros([Method.IPV.shape[0], len(Method.lon_loc), 2, 2])
    # [time_loop, lon_loop, hemi_count, cby or fd]
    jet_th_lev = np.zeros([Method.IPV.shape[0], len(Method.lon_loc), 2, 2])
    #
    jet_H_lev = np.zeros([Method.IPV.shape[0], len(Method.lon_loc), 2])

    # fill with nans
    phi_2PV_out[:, :, :] = np.nan
    theta_2PV_out[:, :, :] = np.nan
    dth_out[:, :, :] = np.nan
    dth_lat_out[:, :] = np.nan
    d2th_out[:, :, :] = np.nan
    jet_best_guess[:, :, :] = np.nan
    mask_jet_number[:, :] = np.nan
    crossing_lat[:, :] = np.nan

    # assign each month in timeseries to a season
    seasons, seasons_num = season_mask(Method.IPV.shape[0])

    # Generate data for testing only.
    testing_make_output = False
    if testing_make_output:
        # Save u and v
        u_zonal_all_time = MeanOverDim(data=Method.u[:, :, :, :], dim=3)
        filename = '{}/STJ_PV_metric.npz'.format(data_dir)
        MakeOutfileSavez_grid(filename, Method.lat, Method.theta_lev,
                              u_zonal_all_time, Method.TropH_theta)

    print_messages = False

    if test_daily:
        file_out_syntax = 'daily'
    else:
        file_out_syntax = 'monthly'


    for time_loop in range(Method.IPV.shape[0]):
        for lon_loop in range(len(Method.lon_loc)):
            for hemi in ['NH', 'SH']:

                if hemi == 'NH':
                    # lat_hemi is the lats to interpolate IPV onto
                    Method.lat_hemi = Method.lat_NH
                    # which IPV line. Default should be 2.0.
                    Method.pv2 = 2.0
                    # for array storage only
                    hemi_count = 0
                    # for testing only
                    STJ_mean, EDJ_mean = 30., 50.
                else:
                    Method.lat_hemi = Method.lat_SH
                    Method.pv2 = -2.0
                    hemi_count = 1
                    STJ_mean, EDJ_mean = -30., -50.

                if slide_method == 'lon_slices':
                    lon_elem = Method.lon_loc[lon_loop]


                # Method Step 1:

                Method.get_phi_theta_2PV(time_loop, hemi, slide_method, lon_elem)

                # Remove any repeated theta and phi pairs if they exist
                Method.unique_elements(hemi, time_loop)

                # finite difference derivative
                Method.PV_differentiate()

                deg_fit = 12
                # polynomial fit and derivative twice
                Method.PolyFit2PV_der(time_loop,deg_fit)

                # Find peaks in poly-fitted data of 2 pv line
                Method.PolyFit2PV_peaks(print_messages, hemi, time_loop)

                # sort the peaks
                Method.PolyFit2PV_SortedLat(hemi, print_messages)

                # find the peak closest to estimated mean - used for testing
                # cby
                (Method.STJ_lat_near_mean_cby, Method.EDJ_lat_near_mean_cby,
                 Method.Additional_lat_cby) = \
                    Method.PolyFit2PV_near_mean(print_messages, STJ_mean, EDJ_mean,
                                                Method.phi_2PV_peak_cby,
                                                Method.local_elem_cby,
                                                Method.STJ_lat_sort_cby,
                                                Method.dtdphi_val_peak_cby,
                                                Method.peak_mag_cby,
                                                Method.sort_index_cby,time_loop)
                # finite
                (Method.STJ_lat_near_mean_fd, Method.EDJ_lat_near_mean_fd,
                 Method.Additional_lat_fd) = \
                    Method.PolyFit2PV_near_mean(print_messages, STJ_mean, EDJ_mean,
                                                Method.phi_2PV_peak_fd,
                                                Method.local_elem_fd,
                                                Method.STJ_lat_sort_fd,
                                                Method.dtdphi_val_peak_fd,
                                                Method.peak_mag_fd, Method.sort_index_fd,time_loop)

                u_zonal = MeanOverDim(data=Method.u[time_loop, :, :, :], dim=2)


                if hemi == 'NH':
                    lat_elem = np.where(Method.lat >= 0)
                else:
                    lat_elem = np.where(Method.lat <= 0)

                # test the shear of the peaks and assign the STJ to peak with most shear
                (Method.shear_elem_cby, Method.shear_max_elem_cby,
                 Method.best_guess_cby) = Method.MaxShear(hemi, u_zonal, lat_elem,
                                                          Method.local_elem_cby, time_loop)

                (Method.shear_elem_fd, Method.shear_max_elem_fd,
                 Method.best_guess_fd) = Method.MaxShear(hemi, u_zonal, lat_elem,
                                                         Method.local_elem_fd, time_loop)

                Method.Trop_H_at_jet(hemi,lat_elem)

                Method.JetIntensity(hemi, u_zonal, lat_elem)

                # vars of interest outside of loop
                phi_2PV_out[time_loop, hemi_count, 0:len(Method.phi_2PV)] = Method.phi_2PV
                theta_2PV_out[time_loop, hemi_count, 0:len(
                    Method.phi_2PV)] = Method.theta_2PV

                #


                # restricted in phi
                # dth_lat_out[time_loop, hemi_count,
                #             0:len(Method.elem)] =  Method.phi_2PV[Method.elem]
                # dth_out[time_loop, hemi_count,
                #         0:len(Method.elem)] =  Method.dtdphi_val[Method.elem]
                # d2th_out[time_loop,
                #         hemi_count, 0:len(Method.d2tdphi2_val[Method.elem])] = \
                #                                        Method.d2tdphi2_val[Method.elem]

                # properties of the method
                crossing_lat[time_loop, hemi_count] = Method.cross_lat
                # mask_jet_number[time_loop, hemi_count] = len(Method.local_elem_cby)
                jet_best_guess[time_loop, lon_loop, hemi_count, 0] = Method.best_guess_cby
                jet_best_guess[time_loop, lon_loop, hemi_count, 1] = Method.best_guess_fd
                jet_intensity[time_loop, lon_loop,
                              hemi_count, 0] = Method.jet_max_wind_cby
                jet_intensity[time_loop, lon_loop, hemi_count, 1] = Method.jet_max_wind_fd
                jet_th_lev[time_loop, lon_loop, hemi_count, 0] = Method.jet_max_theta_cby
                jet_th_lev[time_loop, lon_loop, hemi_count, 1] = Method.jet_max_theta_fd
                jet_H_lev[time_loop, lon_loop, hemi_count] = Method.trop_height_at_STJ



                if((hemi == 'NH' and Method.best_guess_cby > 45) or
                   (hemi == 'SH' and Method.best_guess_cby < -40)):
                    test_with_plots = True
                else:
                    if hemi == 'SH' and Method.best_guess_cby > -25:
                        test_with_plots = True
                    else:
                        test_with_plots = False

                #test_with_plots = False
 
                #for specific test cases create a 2 figure subplot
                #if   ((time_loop >= 419 and time_loop <= 421) #DJF 2014
                #  or (time_loop >= 425 and time_loop <= 427) #JJA 2014 
                #  or (time_loop >= 431 and time_loop <= 433) #DJF 2015 
                #  or (time_loop >= 437 and time_loop <= 439)): #JJA 2015 
 
                if (Method.best_guess_cby > 45):
                     print 'time ', time_loop, ' of interest ', Method.best_guess_cby
                     date_string = DateFromElem(time_loop, 1979)
                     temp_time = True
                else:
                   if hemi == 'NH':
                     temp_time = False
                #if time_loop  == 401 or time_loop == 479 or time_loop == 966 or time_loop ==1262 : #daily interesting
               # if time_loop  == 432 or time_loop == 437: 

                #if temp_time:
                if time_loop  >= 5000:
                  print 'Preparing plot for: time ', time_loop, ' of interest ', Method.best_guess_cby
                  plot_subplot = True
                  test_with_plots = True
                  if hemi == 'NH':
                    #store NH object for plotting
                    Method_NH = copy.deepcopy(Method)
                    u_zonal_NH = copy.deepcopy(u_zonal)
                    lat_elem_NH = copy.deepcopy(lat_elem)
                    test_with_plots = False
                else:
                    plot_subplot = False       
                    test_with_plots = False  
 
                if test_with_plots == True and plot_subplot == False:
                    Method_NH, u_zonal_NH,lat_elem_NH = None, None, None


                if test_with_plots:
                    # pick method to plot
                    method_opt = ['cby', 'fd']
                    method_choice = method_opt[0]

  
                    plot_validation_only = False
                    if plot_validation_only:
                        print('plot for: hemi', hemi, ', time: ', time_loop)
                        plot_validation_for_paper(Method, u_zonal, method_choice, 
                                                  plot_subplot, hemi, time_loop, lat_elem,
                                                  Method_NH, u_zonal_NH, lat_elem_NH)
 
                    plot_u = True
                    if plot_u :
                        print 'Plot validation and map on same plot'
                        make_single, make_with_metric = False, True
                        make_u_plot(Method.u_fname, make_single, make_with_metric, label_p,
                                    u_zonal=u_zonal, u_zonal_NH=u_zonal_NH,
                                    Method=Method, Method_NH=Method_NH, 
                                    lat_elem_SH=lat_elem, lat_elem_NH = lat_elem_NH,
                                    STJ_lat = jet_best_guess[time_loop,0,:,0],
                                    method_choice=method_choice, time=time_loop,
                                    file_out_syntax=file_out_syntax)
        
                    pdb.set_trace()

    Method.trends_annual(jet_best_guess[:, 0, :, 0], jet_intensity[:, 0, :, 0],
           jet_th_lev[:, 0, :, 0],jet_H_lev[:,0,:],file_out_syntax)

    pdb.set_trace()

    if testing_make_output:
        filename = '{0}/STJ_PV_metric_derived_{1}.npz'.format(data_dir,file_out_syntax)
        MakeOutfileSavez_derived(filename, phi_2PV_out,
                                 theta_2PV_out, dth_out, dth_lat_out, d2th_out)

    print 'NH mean position: ', jet_best_guess[:, 0, 0, 0].mean()
    print 'SH mean position: ', jet_best_guess[:, 0, 1, 0].mean()


    if not test_daily:
        # seasonally seperate the data
        Method.SeasonalPeaks(seasons, jet_best_guess[:, 0, :, 0], crossing_lat,
                         jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0],jet_H_lev[:,0,:],file_out_syntax)

        Method.trends_season()

    do_correlations = True
    if do_correlations:
      print 'Get correlations'
      group_opt = ['core','x','h']
      group = group_opt[2]

      # annual values
      Method.AnnualCorrelations(jet_best_guess[:, 0, :, 0], jet_H_lev[:,0,:],
                              jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0], crossing_lat, group)

      if not test_daily:


          Method.MonthlyCorrelations(jet_best_guess[:, 0, :, 0], jet_H_lev[:,0,:],
                               jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0], crossing_lat, group)

          Method.SeasonCorrelations(group)

          Method.CalendarMean(seasons, jet_best_guess[:, 0, :, 0], crossing_lat,
                        jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0],jet_H_lev[:,0,:],group,file_out_syntax)


          PlotPC(group, Method.AnnualPC, Method.SeasonPC,
               Method.MonthlyPC, Method.var_name, Method.diri,file_out_syntax)

      else:    
          plot_daily_stj_ts(jet_best_guess[:, 0, :, 0], crossing_lat,
                            jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0],jet_H_lev[:,0,:] )

          pdb.set_trace()
    #Save output
    Method.SaveOutput(jet_best_guess[:, 0, :, 0], crossing_lat, 
                      jet_intensity[:, 0, :, 0], jet_th_lev[:, 0, :, 0],file_out_syntax)

    pdb.set_trace()

    # second pass - loop at peaks that are not near the seasonal mean
    for time_loop in range(Method.IPV.shape[0]):
        for lon_loop in range(len(Method.lon_loc)):
            for hemi in ['NH', 'SH']:
                if hemi == 'NH':
                    hemi_count = 0
                else:
                    hemi_count = 1
                input_string = str(hemi) + str(':  ') + str(time_loop)
                # Method.validate_near_mean(hemi_count, seasons[time_loop], input_string,
                #                           hemi, time_loop)

    # use the polynomial fitted data peaks to find the steapest part of 2PV line and
    # hence a jet core.
    # Keep in mind that a merged jet state is still technically the latitude of the STJ
    # but the interpretation is just different (i.e. is the STJ defined in summer)
    # keep in mind the goal is not to ID EDJ and STJ it is just the STJ

    pdb.set_trace()

    return output_plotting

def DateFromElem(elem, first_year):
  'For the given input element what is the date. year*12 + month = elem -1'
  month_name = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun','Jul','Aug','Sep','Oct','Nov','Dec']
  year_num = math.floor((elem)/12.0)  #year_num = 0 is first year of data
  year_date = first_year + year_num 
  month_num = int((elem)-(12.*year_num))
  month = month_name[month_num] #month 0 is Jan
  print 'Date is: ' +  str(year_date) + ' ' + str(month)


def DateFromElemDays(elem, first_year):
 
  print 'Not attempted yet as need leap years etc'


# ------------------------
# Testing functions only
# ------------------------


def test_finite_difference(phi_2PV, theta_2PV, dTHdlat_lat, dTHdlat):
    diri = Directory()
    plt.plot(phi_2PV, theta_2PV)
    plt.savefig('{}/testing1.eps'.format(diri.plot_loc))
    #plt.show()

    plt.plot(dTHdlat_lat, dTHdlat)
    plt.savefig('{}/finite_diff_test.eps'.format(diri.plot_loc))
    #plt.show()
    pdb.set_trace()


def Poly_testing(phi_2PV, theta_2PV, theta_cby_val, dtdphi_val, d2tdphi2_val):
    diri = Directory()
    # Plot the fit
    plt.plot(phi_2PV, theta_2PV, c='k', marker='x',
             markersize=8, linestyle='-', label='Data')
    plt.plot(phi_2PV, theta_cby_val, c='r', marker='.',
             markersize=8, linestyle='-', label='cby')
    plt.legend()
    plt.ylim(300, 420)
    plt.savefig('{}/cbyfit_10.eps'.format(diri.plot_loc))
    #plt.show()

    # plot the derivative to identify local maxima in.
    plt.plot(phi_2PV, dtdphi_val, label='dTh/dy')
    plt.plot(phi_2PV, d2tdphi2_val, label='d2Th/dy2')
    plt.legend()
    plt.ylim(-20, 20)
    plt.savefig('{}/cbyfit_10_derivative.eps'.format(diri.plot_loc))
    #plt.show()


def test_crossing_lat(H_lat_hemi, H_hemi, new_lat, h_spline_fit, phi_IPV, theta_IPV,
                      IPV_spline_fit, cross_lat):

    fig1 = plt.figure(figsize=(14, 8))
    ax1 = fig1.add_axes([0.1, 0.15, 0.8, 0.8])

    ax1.plot(H_lat_hemi, H_hemi, c='blue', linestyle=' ',
             marker='.', label='Thermal tropopause')
    ax1.plot(new_lat, h_spline_fit, c='blue', linestyle=' ',
             marker='x', label='Thermal tropopause linear fit')

    ax1.plot(phi_IPV, theta_IPV, c='red', linestyle=' ',
             marker='.', label='Dynamic tropopause')
    ax1.plot(new_lat, IPV_spline_fit, c='red', linestyle=' ',
             marker='x', label='Dynamic tropopause linear fit')

    ax1.plot([cross_lat, cross_lat], [100, 500], c='green',
             linestyle='-', label='Crossing Lat')
    plt.show()
    plt.close()
    pdb.set_trace()


def AttemptMeanFind(hemi, time_loop, dtdphi_val, elem, phi_2PV):
    'Are the peaks near the known lat of the STJ and EDJ?'

    # Isolate data between first and last peak
    detrended = scipy.signal.detrend(dtdphi_val[elem], type='linear')

    max_peaks = argrelmax(detrended)[0].tolist()
    min_peaks = argrelmin(detrended)[0].tolist()

    peaks = np.sort(max_peaks + min_peaks)

    peak_min_val, peak_max_val = detrended[peaks].min(), detrended[peaks].max()

    below_max = np.where(detrended <= peak_max_val)[0]
    above_below_max = np.where(detrended[below_max] >= peak_min_val)[0]

    valid_data = detrended[below_max][above_below_max]
    signal_normalised = valid_data.mean() / np.std(valid_data)

    plt.plot(phi_2PV[elem], dtdphi_val[elem], c='k', linestyle='-', label='Data')
    plt.plot(phi_2PV[elem], detrended, c='k', marker='x',
             markersize=8, linestyle='-', label='Data detrended')
    plt.plot(phi_2PV[above_below_max], detrended[above_below_max], c='r',
             marker='x', markersize=8, linestyle='-', label='Data detrended restricted')
    plt.plot([20, 50], [valid_data.mean(), valid_data.mean()],
             c='r', linestyle='--', label='Mean')
    plt.legend()
    #plt.show()

    # Normalized to unit variance by removing the long-term mean and dividing
    # by the standard deviation.
    valid_range_remove_mean = valid_range - valid_range.mean()
    valid_range_normal = (valid_range_remove_mean /
                          np.std(valid_range_remove_mean))  # sigma of this is 1.0

    plt.plot(phi_2PV[peaks.min():peaks.max()], valid_range_normal, c='k',
             marker='x', markersize=8, linestyle='-', label='Data detrended')

    signal_above_mean = np.where(np.abs(dtdphi_val_normal) >= 0.2)[0].tolist()
    # keep peaks that are in above list
    peak_sig = []
    for i in range(len(local_elem)):
        if local_elem[i] in signal_above_mean:
            peak_sig.append(local_elem[i])

    # Plot the fit
    plt.plot(phi_2PV, data, c='k', marker='x', markersize=8,
             linestyle='-', label='Data scaled z1/100')
    plt.plot(phi_2PV[peak_sig], dtdphi_val_normal[peak_sig], c='r',
             marker='.', markersize=8, linestyle=' ', label='peaks')
    plt.plot(phi_2PV[local_elem], dtdphi_val_normal[local_elem], c='r',
             marker='x', markersize=8, linestyle=' ', label=' allpeaks')
    #plt.show()


def test_max_shear_across_lats(phi_2PV, theta_2PV, lat, lat_elem, theta_lev, u_zonal,time_loop):
    # where is the max shear between surface and 2 pv line?

    fig = plt.figure(figsize=(12, 12))
    ax = fig.add_axes([0.07, 0.2, 0.8, 0.75])

    # plot the zonal mean
    cmap = plt.cm.RdBu_r
    bounds = np.arange(-50, 51, 5.0)
    norm = mpl.colors.BoundaryNorm(bounds, cmap.N)
    # u wind as a contour
    ax.pcolormesh(lat[lat_elem], theta_lev, u_zonal[:, lat_elem][:, 0, :], cmap=cmap,
                  norm=norm)
    ax.set_ylabel('Wind Theta (contour)')
    ax.set_ylim(300, 400)
    ax_cb = fig.add_axes([0.1, 0.1, 0.80, 0.05])
    cbar = mpl.colorbar.ColorbarBase(
        ax_cb, cmap=cmap, norm=norm, ticks=bounds, orientation='horizontal')
    cbar.set_label(r'$\bar{u} ms^{-1}$')

    ax2 = ax.twinx()

    # which of the peaks (from 2pv line and not uwind grid) is closest.
    shear_each_2PV = np.zeros(len(phi_2PV))
    closest_lat_elem = np.zeros(len(phi_2PV))
    closest_theta_elem = np.zeros(len(phi_2PV))

    for i in range(len(phi_2PV)):
        current_lat = phi_2PV[i]
        current_theta = theta_2PV[i]
        closest_lat_elem[i] = FindClosestElem(current_lat, lat[lat_elem],time_loop)[0]
        closest_theta_elem[i] = FindClosestElem(current_theta, theta_lev,time_loop)[0]

        shear_each_2PV[i] = (u_zonal[:, lat_elem][closest_theta_elem[i], 0,
                                                  closest_lat_elem[i]] -
                             u_zonal[:, lat_elem][0, 0, closest_lat_elem[i]])

        ax2.plot(lat[lat_elem][closest_lat_elem[i]], theta_lev[closest_theta_elem[i]],
                 linestyle=' ', c='orange', marker='x', markersize=10)

    # because the grid scales are different - more than one orange cross
    # occurs per theta level. So just select mean on them
    max_elem = np.where(shear_each_2PV == shear_each_2PV.max())[0]

    ax2.set_ylim(300, 400)

    xx = [lat[lat_elem][max_elem].mean(), lat[lat_elem][max_elem].mean()]
    yy = [theta_lev[0], theta_lev[-1]]
    ax2.plot(xx, yy, linestyle='-', c='orange', marker='o', markersize=10, linewidth=2)
    print('shear from 2pv line max:', shear_each_2PV[max_elem].mean())

    ax2.plot(phi_2PV, theta_2PV, linestyle='-', c='k', marker='x',
             markersize=8, label='2PV line - Dynamical Tropopause')

    #plt.show()

    pdb.set_trace()



def print_min_max_mean(output):

    print('{}Assess data{}'.format(32 * '-', 32 * '-'))
    print(' DJF NH: ', output['DJF'][:, 0].min(), output['DJF'][:, 0].max(),
          output['DJF'][:, 0].mean())
    print(' DJF SH: ', output['DJF'][:, 1].min(), output['DJF'][:, 1].max(),
          output['DJF'][:, 1].mean())
    print(' MAM NH: ', output['MAM'][:, 0].min(), output['MAM'][:, 0].max(),
          output['MAM'][:, 0].mean())
    print(' MAM SH: ', output['MAM'][:, 1].min(), output['MAM'][:, 1].max(),
          output['MAM'][:, 1].mean())
    print(' JJA NH: ', output['JJA'][:, 0].min(), output['JJA'][:, 0].max(),
          output['JJA'][:, 0].mean())
    print(' JJA SH: ', output['JJA'][:, 1].min(), output['JJA'][:, 1].max(),
          output['JJA'][:, 1].mean())
    print(' SON NH: ', output['SON'][:, 0].min(), output['SON'][:, 0].max(),
          output['SON'][:, 0].mean())
    print(' SON SH: ', output['SON'][:, 1].min(), output['SON'][:, 1].max(),
          output['SON'][:, 1].mean())


def plot_seasonal_stj_ts(output, cross, STJ_year,file_out_syntax):

    diri = Directory()

    fig = plt.figure(figsize=(14, 8))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax.plot(output['DJF'][:, 0], 'blue', label='NH Winter' +
            ('  {0:.2f}').format(output['DJF'][:, 0].mean()), ls=' ', marker='x')
    ax.plot(output['JJA'][:, 1], 'blue', label='SH Winter' +
            (' {0:.2f}').format(output['JJA'][:, 1].mean()), ls=' ', marker='x')
    ax.plot(output['MAM'][:, 0], 'green', label='NH Spring' +
            ('  {0:.2f}').format(output['MAM'][:, 0].mean()), ls=' ', marker='x')
    ax.plot(output['SON'][:, 1], 'green', label='SH Spring' +
            (' {0:.2f}').format(output['SON'][:, 1].mean()), ls=' ', marker='x')
    ax.plot(output['JJA'][:, 0], 'red', label='NH Summer' +
            ('  {0:.2f}').format(output['JJA'][:, 0].mean()), ls=' ', marker='x')
    ax.plot(output['DJF'][:, 1], 'red', label='SH Summer' +
            (' {0:.2f}').format(output['DJF'][:, 1].mean()), ls=' ', marker='x')
    ax.plot(output['SON'][:, 0], 'orange', label='NH Autumn' +
            ('  {0:.2f}').format(output['SON'][:, 0].mean()), ls=' ', marker='x')
    ax.plot(output['MAM'][:, 1], 'orange', label='SH Autumn' +
            (' {0:.2f}').format(output['MAM'][:, 1].mean()), ls=' ', marker='x')

    ax.set_xlim(-60,60)
    plt.legend(loc=7, ncol=4, bbox_to_anchor=(1.0, -0.1))
    plt.savefig('{0}/index_ts_{1}.eps'.format(diri.plot_loc,file_out_syntax))
    #plt.show()
    #pdb.set_trace()
    plt.close()

    fig = plt.figure(figsize=(14, 8))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax.plot(STJ_year['DJF'][:, 0], 'blue', label='NH Winter' +           
            ('  {0:.2f}').format(np.nanmean(STJ_year['DJF'][:, 0])), ls=' ', marker='x')
    ax.plot(STJ_year['JJA'][:, 1], 'blue', label='SH Winter' +
            (' {0:.2f}').format(np.nanmean(STJ_year['JJA'][:, 1])), ls=' ', marker='x')
    ax.plot(STJ_year['MAM'][:, 0], 'green', label='NH Spring' + 
            (' {0:.2f}').format(np.nanmean(STJ_year['MAM'][:, 0])), ls=' ', marker='x')
    ax.plot(STJ_year['SON'][:, 1], 'green', label='SH Spring' +   
            (' {0:.2f}').format(np.nanmean(STJ_year['SON'][:, 1])), ls=' ', marker='x')
    ax.plot(STJ_year['JJA'][:, 0], 'red', label='NH Summer' +    
            ('  {0:.2f}').format(np.nanmean(STJ_year['JJA'][:, 0])), ls=' ', marker='x')
    ax.plot(STJ_year['DJF'][:, 1], 'red', label='SH Summer' +   
            (' {0:.2f}').format(np.nanmean(STJ_year['DJF'][:, 1])), ls=' ', marker='x')
    ax.plot(STJ_year['SON'][:, 0], 'orange', label='NH Autumn' +  
            ('  {0:.2f}').format(np.nanmean(STJ_year['SON'][:, 0])), ls=' ', marker='x')
    ax.plot(STJ_year['MAM'][:, 1], 'orange', label='SH Autumn' +  
            (' {0:.2f}').format(np.nanmean(STJ_year['MAM'][:, 1])), ls=' ', marker='x')
    #plt.xticks(np.arange(0, 450, 24))
    plt.xticks(np.arange(0, 450, 24),np.arange(1979, 2016, 2))
    plt.yticks(np.arange(-60, 61, 10))
    minorLocator   = MultipleLocator(6)
    ax.xaxis.set_minor_locator(minorLocator)
    minorLocator   = MultipleLocator(5)
    ax.yaxis.set_minor_locator(minorLocator)
    plt.legend(loc=7, ncol=4, bbox_to_anchor=(0.982, -0.1), numpoints=1)
    plt.savefig('{}/index_ts_year.eps'.format(diri.plot_loc))
    #plt.show()
    plt.close()
    #pdb.set_trace()

    #plot a cut down section
    start,end = 216, 288
    fig = plt.figure(figsize=(8, 4))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    x = np.arange(start, end, 1)
    ax.plot(x,STJ_year['DJF'][start-1:end-1, 0], 'blue',  ls=' ', marker='x')
    ax.plot(x,STJ_year['MAM'][start-1:end-1, 0], 'green', ls=' ', marker='x')
    ax.plot(x,STJ_year['JJA'][start-1:end-1, 0], 'red',   ls=' ', marker='x')
    ax.plot(x,STJ_year['SON'][start-1:end-1, 0], 'orange',ls=' ', marker='x')
    plt.xticks(np.arange(216,288+1, 12),np.arange(1997, 2004, 1))
    ax.set_xlim(start, end)
    plt.yticks(np.arange(15, 45+1, 5))
    minorLocator   = MultipleLocator(3)
    ax.xaxis.set_minor_locator(minorLocator)
    minorLocator   = MultipleLocator(2)
    ax.yaxis.set_minor_locator(minorLocator)
    plt.savefig('{}/index_ts_year_{}-{}_NH.eps'.format(diri.plot_loc,start,end))
    #plt.show()
    plt.close()

    start,end = 216, 288
    fig = plt.figure(figsize=(8, 4))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    x = np.arange(start, end, 1)
    ax.plot(x,STJ_year['JJA'][start-1:end-1, 1], 'blue',  ls=' ', marker='x')
    ax.plot(x,STJ_year['SON'][start-1:end-1, 1], 'green', ls=' ', marker='x')
    ax.plot(x,STJ_year['DJF'][start-1:end-1, 1], 'red',   ls=' ', marker='x')
    ax.plot(x,STJ_year['MAM'][start-1:end-1, 1], 'orange',ls=' ', marker='x')
    plt.xticks(STJ_year['DJF'][start-1:end-1, 0],np.arange(1997, 2003+1, 12))
    plt.xticks(np.arange(start,end+1, 12),np.arange(1997, 2004, 1))
    ax.set_xlim(start, end)
    plt.yticks(np.arange(-45, -15+1, 5))
    minorLocator   = MultipleLocator(3)
    ax.xaxis.set_minor_locator(minorLocator)
    minorLocator   = MultipleLocator(2)
    ax.yaxis.set_minor_locator(minorLocator)
    plt.savefig('{}/index_ts_year_{}-{}_SH.eps'.format(diri.plot_loc,start,end))
    #plt.show()
    plt.close()



    # plot the crossing points

    fig = plt.figure(figsize=(14, 8))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax.plot(cross['DJF'][:, 0], 'blue', label='NH Winter', ls=' ', marker='x')
    ax.plot(cross['JJA'][:, 1], 'blue', label='SH Winter', ls=' ', marker='x')
    ax.plot(cross['MAM'][:, 0], 'green', label='NH Spring', ls=' ', marker='x')
    ax.plot(cross['SON'][:, 1], 'green', label='SH Spring', ls=' ', marker='x')
    ax.plot(cross['JJA'][:, 0], 'red', label='NH Summer', ls=' ', marker='x')
    ax.plot(cross['DJF'][:, 1], 'red', label='SH Summer', ls=' ', marker='x')
    ax.plot(cross['SON'][:, 0], 'orange', label='NH Autumn', ls=' ', marker='x')
    ax.plot(cross['MAM'][:, 1], 'orange', label='SH Autumn', ls=' ', marker='x')
    ax.set_ylim(-25, 25)
    plt.legend(loc=7, ncol=4, bbox_to_anchor=(1.0, -0.1))
    plt.savefig('{}/cross.eps'.format(diri.plot_loc))
    #plt.show()
    plt.close()

def plot_daily_stj_ts(pos, cross, intense, theta, H_above):

    num_may = 31  
    num_june = 30
    num_mm = num_may + num_june #number of days in a 'year'

    years = len(pos)/(num_mm)
    may_elem  = np.zeros(num_may*years)
    june_elem = np.zeros(num_june*years)

    time_elem = np.arange(0,len(pos),1)

    for i in xrange(years):
        may_elem[i*num_may:(i*num_may+num_may)]  = time_elem[(num_mm)*i:((num_mm)*i+num_may)] 
        june_elem[i*num_june:(i*num_june+num_june)] = time_elem[((num_mm)*i+num_may):((num_mm)*i+num_mm)]

    may_elem = may_elem.tolist()
    june_elem = june_elem.tolist()

    diri = Directory()

    fig = plt.figure(figsize=(14, 8))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax.plot(time_elem[may_elem],  pos[may_elem,0], 'green', label='NH May' +  ('  {0:.2f}').format(pos[may_elem,0].mean()), ls=' ', marker='x')
    ax.plot(time_elem[june_elem], pos[june_elem,0], 'red', label='NH June' +  ('  {0:.2f}').format(pos[june_elem,0].mean()), ls=' ', marker='x')
    #ax.plot(time_elem[may_elem],  pos[may_elem,1], 'green', label='SH May' +  ('  {0:.2f}').format(pos[may_elem,1].mean()), ls=' ', marker='x')
    #ax.plot(time_elem[june_elem], pos[june_elem,1], 'red', label='SH June' +  ('  {0:.2f}').format(pos[june_elem,1].mean()), ls=' ', marker='x')
    plt.legend(loc=1, ncol=2, numpoints=1)
    ax.set_xlim(0, len(pos)+1)
    plt.xticks(np.arange(0, len(pos), 122),np.arange(1979, 2016, 2))
    ax.set_ylim(10,60)
    plt.yticks(np.arange(10, 61, 5))
    elem_for_yr_tks= np.arange(0, len(pos), 122)
    tick_sep=np.arange(0, len(pos), 61)
    for i in xrange(len(tick_sep)):
      ax.plot([tick_sep[i],tick_sep[i]],[10,60], 'gray',ls=':')
    plt.savefig('{0}/index_ts_may_june.eps'.format(diri.plot_loc))
    plt.show()
    plt.close()
    #pdb.set_trace()

    d_01_1979 = date(1979, 1, 1)
    d_01_2003 = date(2003, 12, 31)
    day_diff = d_01_2003 - d_01_1979


    #absolute elements of may-june days 1979-2003
    may_start = 1098 #May 79
    june_end = 1524#-61  #June 03 - June 2002 was the goal
    may_slice = np.where(np.logical_and(np.array(may_elem)>=may_start,np.array(may_elem)<=june_end))[0] #years * mday days = 217 element
    june_slice = np.where(np.logical_and(np.array(june_elem)>=may_start,np.array(june_elem)<=june_end ))[0] #years * mday days = 210 element

    may_absolute_elem = (np.array(may_elem)[may_slice]).tolist()
    june_absolute_elem = (np.array(june_elem)[june_slice]).tolist()

    fig = plt.figure(figsize=(14, 8))
    ax = fig.add_axes([0.1, 0.2, 0.8, 0.75])
    ax.plot(time_elem[may_absolute_elem],pos[may_absolute_elem, 0], 'green', label='NH May', ls=' ', marker='x')
    ax.plot(time_elem[june_absolute_elem],pos[june_absolute_elem, 0], 'red', label='NH June', ls=' ', marker='x')
    #horizontal lines to seperate years
    #plt.legend(loc=7, ncol=2, bbox_to_anchor=(1.0, -0.1))
    elem_for_yr_tks = np.arange(may_start,june_end+1, 61)
    plt.xticks(elem_for_yr_tks,np.arange(1997, 2004, 1))
    ax.set_xlim(may_start,  june_end)
    ax.set_ylim(15,45)
    for i in xrange(len(elem_for_yr_tks)):
      ax.plot([elem_for_yr_tks[i],elem_for_yr_tks[i]],[15,55], 'gray',ls=':')
    ax.set_xlim(may_start,june_end-60)
    plt.savefig('{0}/index_ts_may_june_97_03.eps'.format(diri.plot_loc))
    plt.show()
    pdb.set_trace()
    plt.close()

    pdb.set_trace()

def GetCorrelation(hemi, num_var, var_name, data):

    # -----------Correlations-----------------
    # null hypothesis is that linear regression slope and zero and the
    # two timeseries are unrelated.
    # if p <=0.05 then reject the null

    corr = np.zeros([num_var, num_var, 2])

    for hemi_count in range(2):
        for i in range(num_var):
            for j in range(num_var):

                # print 'Correlation with: ', var_name[i], var_name[j], 'in ',
                # hemi[hemi_count]

                slope, intercept, r_value, p_value, std_err = mstats.linregress(
                    data[:, i, hemi_count], data[:, j, hemi_count])

                if p_value <= 0.05:
                    # print '                                significant correlation',
                    # 100-p_value*100, r_value
                    corr[i, j, hemi_count] = r_value
                else:
                    # print '                                weak correlation ',
                    # 100-p_value*100, r_value
                    corr[i, j, hemi_count] = 0.0
    return corr
