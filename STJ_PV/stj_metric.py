# -*- coding: utf-8 -*-
"""Calculate the position of the subtropical jet in both hemispheres."""
import subprocess
import yaml
import xarray as xr
import numpy as np
import numpy.polynomial as poly
from scipy import signal as sig

import STJ_PV.data_out as dio

from netCDF4 import num2date, date2num
import pandas as pd
import xarray as xr
from STJ_PV import utils

try:
    from eddy_terms import Kinetic_Eddy_Energies
except ModuleNotFoundError:
    print('Eddy Terms Function not available, STJKangPolvani not available')

try:
    GIT_ID = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip()
except subprocess.CalledProcessError:
    GIT_ID = 'NONE'


class STJMetric(object):
    """Generic Class containing Sub Tropical Jet metric methods and attributes."""

    def __init__(self, name=None, data=None, props=None):
        """
        Initialize a subtropical jet metric.

        Parameters
        ----------
        name : string
            Name of this type of method
        data : InputData
            Object containing required input data
        props : JetFindRun
            Properties about the current jet finding attempt, including log file

        """
        self.name = name
        self.data = data
        self.props = props.config
        self.log = props.log
        self.out_data = {}
        self.time = None
        self.hemis = None
        self.debug_data = {}
        self.plot_idx = 0

    def save_jet(self):
        """Save jet position to file."""
        # Setup metadata for output variables
        props = {'lat': {'standard_name': 'jet_latitude',
                         'descr': 'Latitude of subtropical jet',
                         'units': 'degrees_north'},
                 'intens': {'standard_name': 'jet_intensity',
                            'descr': 'Intensity of subtropical jet',
                            'units': 'm s-1'},
                 'theta': {'standard_name': 'jet_theta',
                           'descr': 'Theta level of subtropical jet',
                           'units': 'K'}}

        for out_var in self.out_data:
            # Clean up dimension labels
            for drop_var in ['pv', self.data.cfg['lat']]:
                if drop_var in self.out_data[out_var].coords:
                    self.out_data[out_var] = self.out_data[out_var].drop(drop_var)
            prop_name = out_var.split('_')[0]
            self.out_data[out_var] = self.out_data[out_var].assign_attrs(props[prop_name])

        out_dset = xr.Dataset(self.out_data)
        self.log.info("WRITE TO {output_file}".format(**self.props))
        file_attrs = {'commit-id': GIT_ID, 'run_props': yaml.safe_dump(self.props)}
        out_dset = out_dset.assign_attrs(file_attrs)
        out_dset.to_netcdf(self.props['output_file'] + '.nc')

    def append(self, other):
        """Append another metric's intensity, latitude, and theta positon to this one."""
        for var_name in self.out_data:
            self.out_data[var_name] = xr.concat((self.out_data[var_name],
                                                 other.out_data[var_name]),
                                                dim=self.data.cfg['time'])


class STJPV(STJMetric):
    """
    Subtropical jet position metric using dynamic tropopause on isentropic levels.

    Parameters
    ----------
    props : :py:meth:`~STJ_PV.run_stj.JetFindRun`
        Class containing properties about the current search for the STJ
    data : :py:meth:`~STJ_PV.input_data.InputData`
        Input data class containing a year (or more) of required data

    """
    def __init__(self, props, data):
        """Initialise Metric using PV Gradient Method."""
        name = 'PVGrad'
        super(STJPV, self).__init__(name=name, props=props, data=data)
        # Some config options should be properties for ease of access
        self.pv_lev = self.props['pv_value']
        self.fit_deg = self.props['fit_deg']
        self.min_lat = self.props['min_lat']

        if self.props['poly'].lower() in ['cheby', 'cby', 'cheb', 'chebyshev']:
            self.pfit = poly.chebyshev.chebfit
            self.pder = poly.chebyshev.chebder
            self.peval = poly.chebyshev.chebval

        elif self.props['poly'].lower() in ['leg', 'legen', 'legendre']:
            self.pfit = poly.legendre.legfit
            self.pder = poly.legendre.legder
            self.peval = poly.legendre.legval

        elif self.props['poly'].lower() in ['poly', 'polynomial']:
            self.pfit = poly.polynomial.polyfit
            self.pder = poly.polynomial.polyder
            self.peval = poly.polynomial.polyval

        # Initialise latitude & theta output dicts
        self.out_data = {}

    def _poly_deriv(self, lat, data, deriv=1):
        """
        Calculate the `deriv`^th derivative of a one-dimensional array w.r.t. latitude.

        Parameters
        ----------
        data : array_like
            1D array of data, same shape as `self.data.lat`
        y_s, y_e : integers, optional
            Start and end indices of subset, default is None
        deriv : integer, optional
            Number of derivatives of `data` to take

        Returns
        -------
        poly_der : array_like
            1D array of 1st derivative of data w.r.t. latitude between indices y_s and y_e

        """
        # Determine where data is valid...Intel's Lin Alg routines fail when trying to do
        # a least squares fit on array with np.nan, use where it's valid to do the fit
        valid = np.isfinite(data)
        try:
            poly_fit = self.pfit(lat[valid], data[valid], self.fit_deg)
        except TypeError as err:
            # This can happen on fitting the polynomial:
            # `raise TypeError("expected non-empty vector for x")`
            # If that's the error we get, just set the position to 0,
            # which is later masked, otherwise raise the error
            if 'non-empty' in err.args[0]:
                poly_fit = np.zeros(self.fit_deg)
            else:
                raise

        poly_der = self.peval(lat, self.pder(poly_fit, deriv))

        return poly_der, (poly_fit, lat[valid])

    def set_hemis(self, shemis):
        """
        Select hemisphere data.

        This function sets `self.hemis` to be an length N list of slices such that only
        the desired hemisphere is selected with N-D data (e.g. uwind and ipv) along all
        other axes. It also returns the latitude for the selected hemisphere, an index
        to select the hemisphere in output arrays, and the extrema function to find
        min/max of PV derivative in a particular hemisphere.

        Parameters
        ----------
        shemis : boolean
            If true - use southern hemisphere data, if false, use NH data

        Returns
        -------
        lat : array_like
            Latitude array from selected hemisphere
        hidx : int
            Hemisphere index 0 for SH, 1 for NH
        extrema : function
            Function used to identify extrema in meridional PV gradient, either
            :func:`scipy.signal.argrelmax` if SH, or :func:`scipy.signal.argrelmin`
            for NH

        """
        lats = (self.props['min_lat'], self.props['max_lat'])

        if shemis:
            self.hemis = self.data[self.data.cfg['lat']] < 0
            extrema = sig.argrelmax
            hem_s = 'sh'
            if lats[0] > 0 and lats[1] > 0:
                # Lats are positive, multiply by -1 to get positive for SH
                lats = (-lats[0], -lats[1])
        else:
            self.hemis = self.data[self.data.cfg['lat']] > 0
            extrema = sig.argrelmin
            hem_s = 'nh'
            if lats[0] < 0 and lats[1] < 0:
                # Lats are negative, multiply by -1 to get positive for NH
                lats = (-lats[0], -lats[1])
        return extrema, lats, hem_s

    def isolate_pv(self, pv_lev):
        """
        Get the potential temperature, zonal wind and zonal wind shear for a PV level.

        Parameters
        ----------
        pv_lev : float
            PV value (for a particular hemisphere, >0 for NH, <0 for SH) on which to
            interpolate potential temperature and wind
        theta_bnds : tuple, optional
            Start and end theta levels to use for interpolation. Default is None,
            if None, use all theta levels, otherwise restrict so
            theta_bnds[0] <= theta <= theta_bnds[1]

        Returns
        -------
        theta_xpv : array_like
            N-1 dimensional array (where `self.data.ipv` is N-D) of potential temperature
            on `pv_lev` PVU
        uwnd_xpv : array_like
            N-1 dimensional array (where `self.data.uwnd` is N-D) of zonal wind
            on `pv_lev` PVU
        ushear : array_like
            Wind shear between uwnd_xpv and "surface", meaning the lowest valid level

        """
        if 'theta_s' in self.data.cfg and 'theta_e' in self.data.cfg:
            theta_bnds = (self.data.cfg['theta_s'], self.data.cfg['theta_e'])
            assert theta_bnds[0] < theta_bnds[1], 'Start level not strictly less than end'
            theta_bnds = slice(*theta_bnds)
        else:
            theta_bnds = slice(None)

        lev_name = self.data.cfg['lev']
        subset = {lev_name: theta_bnds}

        theta_xpv = utils.xrvinterp(self.data[lev_name].sel(**subset),
                                    self.data.ipv.where(self.hemis).sel(**subset),
                                    pv_lev, levname=lev_name, newlevname='pv')

        uwnd_xpv = utils.xrvinterp(self.data.uwnd.where(self.hemis).sel(**subset),
                                   self.data.ipv.where(self.hemis).sel(**subset),
                                   pv_lev, levname=lev_name, newlevname='pv')

        ushear = self._get_max_shear(uwnd_xpv.squeeze())
        return theta_xpv.squeeze(), uwnd_xpv.squeeze(), ushear

    def find_jet(self, shemis=True):
        """
        Find the subtropical jet using input parameters.

        Parameters
        ----------
        shemis : logical, optional
            If True, find jet position in Southern Hemisphere, if False, find N.H. jet

        """
        if shemis and self.pv_lev < 0 or not shemis and self.pv_lev > 0:
            pv_lev = np.array([self.pv_lev]) * 1e-6
        else:
            pv_lev = -1 * np.array([self.pv_lev]) * 1e-6

        extrema, lats, hem_s = self.set_hemis(shemis)
        self.log.info('COMPUTING THETA/UWND ON %.1f PVU', pv_lev * 1e6)
        # Get theta on PV==pv_level
        theta_xpv, uwnd_xpv, ushear = self.isolate_pv(pv_lev)

        # Shortcut for latitude variable name, since it's used a lot
        vlat = self.data.cfg['lat']

        # Restrict theta and shear between our min / max latitude from config file
        # that was processed by self.set_hemis
        _theta = theta_xpv.sel(**{vlat: slice(*lats)})
        if _theta[vlat].shape[0] == 0:
            # If the selection is empty along the latitude axis, that means
            # the selection is the wrong way around, so flip it before moving on
            lats = lats[::-1]
            _theta = theta_xpv.sel(**{vlat: slice(*lats)})

        # Coordinates to ravel along, we're interested in doing computations along
        # the latitude axis, so this is all the dimensions except that
        cell_coords = (self.data.cfg['time'], self.data.cfg['lon'])

        # Stack the shear and theta_xpv so their dims are [lat, cell], so that
        # the compuation can be parallelised, since each lon and time are treated
        # as independent
        _shear = ushear.stack(cell=cell_coords).squeeze().sel(**{vlat: slice(*lats)})
        _theta = _theta.stack(cell=cell_coords).squeeze()

        self.log.info('COMPUTING JET POSITION FOR %d', self.data.year)
        # Set up computation of all the jet latitudes at once using self.find_single_jet
        # The input_core_dims is a list of lists, that tells xarray/dask that the
        # arguments _theta, _theta.lat, and _shear are passed to self.find_single_jet
        # with that dimension intact. The kwargs argument passes keyword args to the
        # self.find_single_jet
        jet_lat = xr.apply_ufunc(self.find_single_jet, _theta, _theta[vlat], _shear,
                                 input_core_dims=[[vlat], [vlat], [vlat]],
                                 vectorize=True, dask='parallelized',
                                 kwargs={'extrema': extrema},
                                 output_dtypes=[float])

        # Perform the computation, and unstack so now jet_lat's dims are [time, lon]
        jet_lat = jet_lat.compute().unstack('cell')
        # Select the data for level and intensity by the latitudes generated
        jet_theta = theta_xpv.sel(**{vlat: jet_lat})
        jet_intens = uwnd_xpv.sel(**{vlat: jet_lat})

        # This masks our xarrays of intrest where the jet_lat == 0.0, which is set
        # whenever there is invalid data for a particular cell
        jet_intens = jet_intens.where(jet_lat != 0.0)
        jet_theta = jet_theta.where(jet_lat != 0.0)
        jet_lat = jet_lat.where(jet_lat != 0.0)

        # If we're interested in mean / median, take those
        if self.props['zonal_opt'].lower() == 'mean':
            jet_intens = jet_intens.mean(dim=self.data.cfg['lon'])
            jet_theta = jet_theta.mean(dim=self.data.cfg['lon'])
            jet_lat = jet_lat.mean(dim=self.data.cfg['lon'])

        elif self.props['zonal_opt'].lower() == 'median':
            jet_intens = jet_intens.median(dim=self.data.cfg['lon'])
            jet_theta = jet_theta.median(dim=self.data.cfg['lon'])
            jet_lat = jet_lat.median(dim=self.data.cfg['lon'])

        # Put the parameters into place for this hemisphere
        self.out_data['intens_{}'.format(hem_s)] = jet_intens
        self.out_data['theta_{}'.format(hem_s)] = jet_theta
        self.out_data['lat_{}'.format(hem_s)] = jet_lat

    def _get_max_shear(self, uwnd_xpv):
        """Get maximum wind-shear between surface and PV surface."""
        # Our zonal wind data is on isentropic levels. Lower levels are bound to be below
        # the surface in some places, so we need to use the lowest valid wind level as
        # the surface, so do some magic to make that happen.
        cell_dims = (self.data.cfg['time'], self.data.cfg['lat'], self.data.cfg['lon'])
        u_stack = self.data.uwnd.stack(cell=cell_dims).squeeze()
        uwnd_sfc = xr.apply_ufunc(lowest_valid, u_stack,
                                  input_core_dims=[[self.data.cfg['lev']]],
                                  vectorize=True, dask='parallelized',
                                  output_dtypes=[float])
        uwnd_sfc = uwnd_sfc.unstack('cell').compute()

        return uwnd_xpv.squeeze() - uwnd_sfc.where(self.hemis)

    def find_single_jet(self, theta_xpv, lat, ushear, extrema, debug=False):
        """
        Find jet location for a 1D array of theta on latitude.

        Parameters
        ----------
        theta_xpv : array_like
            Theta on PV level as a function of latitude
        lat : array_like
            1D array of latitude same shape as theta_xpv from :py:meth:`~isolate_pv`
        ushear : array_like
            1D array along latitude axis of maximum surface - troposphere u-wind shear
        debug : boolean
            If True, returns debugging information about how jet position is found,
            if False (default) returns only jet location

        Returns
        -------
        jet_loc : int
            If debug is False, Index of jet location on latitude axis
        jet_loc, jet_loc_all, dtheta, theta_fit, lat, y_s, y_e  : tuple
            If debug is True, return lots of stuff
            TODO: document this better!!

        """
        # Find derivative of dynamical tropopause
        dtheta, theta_fit = self._poly_deriv(lat, theta_xpv)

        jet_loc_all = extrema(dtheta)[0].astype(int)
        select = self.select_jet(jet_loc_all, ushear)
        if np.max(np.abs(theta_fit[0])) == 0.0:
            # This means there was a TypeError in _poly_deriv so probably
            # none of the theta_xpv data is valid for this time/lon, so
            # set the output latitude to be 0, so it can be masked out
            out_lat = 0.0
        else:
            out_lat = lat[select]

        if debug:
            output = out_lat, jet_loc_all, dtheta, theta_fit, lat
        else:
            output = out_lat

        return output

    def select_jet(self, locs, ushear):
        """
        Select correct jet latitude given list of possible jet locations.

        Parameters
        ----------
        locs : list
            List of indicies of jet locations
        ushear : array_like
            1D array along latitude axis of maximum surface - troposphere u-wind shear

        Returns
        -------
        jet_loc : int
            Index of the jet location. Between [`0`, `lat.shape[0] - 1`]

        Notes
        -----
        * If the list of locations is empty, return ``0`` as the location, this is
          interpreted by :py:meth:`~find_jet` as missing.

        * If the list of locations is exactly one return that location.

        * Otherwise use the location with maximum zonal wind shear between lowest
          provided level and the dynamical tropopause.

        """
        if len(locs) == 0:
            # A jet has not been identified at this time/location, set the position
            # to zero so it can be masked out when the zonal median is performed
            jet_loc = 0

        elif len(locs) == 1:
            # This essentially converts a list of length 1 to a single int
            jet_loc = locs[0]

        elif len(locs) > 1:
            # The jet location, if multiple peaks are identified, should be the one
            # with maximum wind shear between the jet level and the surface
            ushear_max = np.argmax(ushear[locs])
            jet_loc = locs[ushear_max]

        return jet_loc


class STJMaxWind(STJMetric):
    """
    Subtropical jet position metric: maximum zonal mean zonal wind on a pressure level.

    Parameters
    ----------
    props : :py:meth:`~STJ_PV.run_stj.JetFindRun`
        Class containing properties about the current search for the STJ
    data : :py:meth:`~STJ_PV.input_data.InputData`
        Input data class containing a year (or more) of required data

    """

    def __init__(self, props, data):
        """Initialise Metric using PV Gradient Method."""
        name = 'UMax'
        super(STJMaxWind, self).__init__(name=name, props=props, data=data)

        # Some config options should be properties for ease of access
        self.pres_lev = self.props['pres_level']
        self.min_lat = self.props['min_lat']

        # Initialise latitude & theta output arrays with correct shape
        dims = self.data.uwnd.shape

        self.jet_lat = np.zeros([2, dims[0]])
        self.jet_intens = np.zeros([2, dims[0]])

        self.time = self.data.time[:]
        self.tix = None
        self.xix = None

    def find_jet(self, shemis=True):
        """
        Find the subtropical jet using input parameters.

        Parameters
        ----------
        shemis : logical, optional
            If True, find jet position in Southern Hemisphere, if False, find N.H. jet

        """
        # Find axis
        lat_axis = self.data.uwnd.shape.index(self.data.lat.shape[0])

        if self.data.uwnd.shape.count(self.data.lat.shape[0]) > 1:
            # Log a message about which matching dimension used since this
            # could be time or lev or lon if ntimes, nlevs or nlons == nlats
            self.log.info('ASSUMING LAT DIM IS: {} ({})'.format(lat_axis,
                                                                self.data.uwnd.shape))

        self.hemis = [slice(None)] * self.data.uwnd.ndim

        if shemis:
            self.hemis[lat_axis] = self.data.lat < 0
            lat = self.data.lat[self.data.lat < 0]
            hidx = 0
        else:
            self.hemis[lat_axis] = self.data.lat > 0
            lat = self.data.lat[self.data.lat > 0]
            hidx = 1

        # Get uwnd on pressure level
        if self.data.uwnd[self.hemis].shape[1] != 1:
            uwnd_p = self.data.uwnd[self.hemis][:, self.data.lev == self.pres_lev, ...]
        else:
            uwnd_p = self.data.uwnd[self.hemis]

        uwnd_p = np.squeeze(uwnd_p)
        dims = uwnd_p.shape

        self.log.info('COMPUTING JET POSITION FOR %d TIMES HEMIS: %d', dims[0], hidx)
        for tix in range(dims[0]):
            if tix % 50 == 0 and dims[0] > 50:
                self.log.info('COMPUTING JET POSITION FOR %d', tix)
            self.tix = tix
            jet_loc = np.zeros(dims[-1])
            for xix in range(dims[-1]):
                self.xix = xix
                jet_loc[xix] = self.find_single_jet(uwnd_p[tix, :, xix])

            jet_lat = np.ma.masked_where(jet_loc == 0, lat[jet_loc.astype(int)])
            self.jet_lat[hidx, tix] = np.ma.mean(jet_lat)

            jet_intens = np.nanmean(uwnd_p[tix, :, :], axis=-1)
            jet_intens = np.ma.masked_where(jet_loc == 0,
                                            jet_intens[jet_loc.astype(int)])
            self.jet_intens[hidx, tix] = np.ma.mean(jet_intens)

    def find_single_jet(self, uwnd):
        """
        Find the position of the maximum zonal wind of a 1D array of zonal wind.

        Parameters
        ----------
        uwnd : array_like
            1D array of zonal wind of the same shape as input latitude.

        Returns
        -------
        u_max_loc : integer
            Integer position of maximum wind (argmax)

        """
        # Yeah, this is really simple, so what? Maybe someday this function grows
        # up to do more than just the argmax, you don't know!
        return np.argmax(uwnd)

class STJKangPolvani(STJMetric):
    """
    Subtropical jet position metric: Kang and Polvani 2010.

    Parameters
    ----------
    props : :py:meth:`~STJ_PV.run_stj.JetFindRun`
        Class containing properties about the current search for the STJ
    data : :py:meth:`~STJ_PV.input_data.InputData`
        Input data class containing a year (or more) of required data
    """

    def __init__(self, props, data):

        """Initialise Metric using Kang and Polvani Method."""

        name = 'KangPolvani'
        super(STJKangPolvani, self).__init__(name=name, props=props, data=data)

        self.dates = pd.DatetimeIndex(num2date(self.data.time[:], self.data.time_units))

        self.jet_intens_daily = np.zeros([2, self.dates.shape[0]])
        # Seasonal mean is expected
        self.jet_lat_daily = np.zeros([2, self.dates.shape[0]])

        # Seasonal and monthly mean positions
        # self.jet_lat_sm = np.zeros([2, 4])
        # self.jet_lat_mm = np.zeros([2, 12])

        # Output monthly means for comparison
        num_mon = len(np.unique(self.dates.year)) * 12
        self.jet_lat = np.zeros([2, num_mon])
        self.jet_intens = np.zeros([2, num_mon])
        self.wh_1000 = None
        self.wh_200 = None


    def find_jet(self, shemis=True):
        """
        Find the subtropical jet using input parameters.

        Parameters
        ----------
        shemis : logical, optional
            If True, find jet position in Southern Hemisphere, if False, find N.H. jet

        """

        lat_elem, hidx = self.set_hemis(shemis)

        uwnd, vwnd = self._prep_data(lat_elem)
        del_f = self.get_flux_div(uwnd, vwnd, lat_elem)
        self.get_jet_lat(del_f, np.mean(uwnd, axis=-1), self.data.lat[lat_elem], hidx)

    def set_hemis(self, shemis):
        """
        Select hemisphere data.

        This function sets `self.hemis` to be an length N list of slices such that only
        the desired hemisphere is selected with N-D data (e.g. uwind and ipv) along all
        other axes. It also returns the latitude for the selected hemisphere, an index
        to select the hemisphere in output arrays, and the extrema function to find
        min/max of PV derivative in a particular hemisphere.

        Parameters
        ----------
        shemis : boolean
            If true - use southern hemisphere data, if false, use NH data

        Returns
        -------
        lat_elem : array_like
            Latitude element locations for given hemisphere
        hidx : int
            Hemisphere index 0 for SH, 1 for NH

        """

        lat_axis = self.data.uwnd.shape.index(self.data.lat.shape[0])

        if self.data.uwnd.shape.count(self.data.lat.shape[0]) > 1:
            # Log a message about which matching dimension used since this
            # could be time or lev or lon if ntimes, nlevs or nlons == nlats
            self.log.info('ASSUMING LAT DIM IS: {} ({})'.format(lat_axis,
                                                                self.data.uwnd.shape))

        self.hemis = [slice(None)] * self.data.uwnd.ndim

        if shemis:
            self.hemis[lat_axis] = self.data.lat < 0
            lat_elem = np.where(self.data.lat < 0)[0]
            # needed to find the seasonal mean jet let from each zero crossing latitude
            hidx = 0
        else:
            self.hemis[lat_axis] = self.data.lat > 0
            lat_elem = np.where(self.data.lat > 0)[0]
            hidx = 1

        return lat_elem, hidx

    def _prep_data(self, lat_elem):

        # Test if pressure is in Pa or hPa
        if self.data.lev.max() < 1100.0:
            self.data.lev = self.data.lev * 100.

        # Only compute flux div at 200hpa
        self.wh_200 = np.where(self.data.lev == 20000.)[0]
        assert len(self.wh_200) != 0, 'Cant find 200 hpa level'

        # Need surface data for calc shear
        self.wh_1000 = np.where(self.data.lev == 100000.)[0]
        assert len(self.wh_1000) != 0, 'Cant find 1000 hpa level'

        uwnd = xr.DataArray(self.data.uwnd[:, :, lat_elem, :],
                            coords=[self.dates,
                                    self.data.lev,
                                    self.data.lat[lat_elem],
                                    self.data.lon],
                            dims=['time', 'pres', 'lat', 'lon'])

        vwnd = xr.DataArray(self.data.vwnd[:, :, lat_elem, :],
                            coords=[self.dates,
                                    self.data.lev,
                                    self.data.lat[lat_elem],
                                    self.data.lon],
                            dims=['time', 'pres', 'lat', 'lon'])

        return uwnd, vwnd

    def get_flux_div(self, uwnd, vwnd, lat_elem):
        """
        Calculate the meridional eddy momentum flux divergence

        """

        lat = self.data.lat[lat_elem]

        k_e = Kinetic_Eddy_Energies(uwnd.values[:, self.wh_200, :, :],
                                    vwnd.values[:, self.wh_200, :, :],
                                    lat, self.props['pres_level'], self.data.lon)
        k_e.get_components()
        k_e.calc_momentum_flux()

        del_f = xr.DataArray(np.squeeze(k_e.del_f),
                             coords=[self.dates, self.data.lat[lat_elem]],
                             dims=['time', 'lat'])
        return del_f

    def get_jet_lat(self, del_f, uwnd, lat, hidx):
        """
        Find the 200hpa zero crossing of the meridional eddy momentum flux divergence

        """

        signchange = ((np.roll(np.sign(del_f), 1) - np.sign(del_f)) != 0).values
        signchange[:, 0], signchange[:, -1] = False, False

        stj_lat = np.zeros(uwnd.shape[0])
        stj_int = np.zeros(uwnd.shape[0])

        for t in range(uwnd.shape[0]):
            shear = (uwnd[t, self.wh_200, signchange[t, :]].values -
                     uwnd[t, self.wh_1000, signchange[t, :]].values)
            stj_lat[t] = lat[signchange[t, :]][np.argmax(shear)]
            stj_int[t] = uwnd[t, self.wh_200[0], np.where(lat == stj_lat[t])[0]].values

        # Output the monthly mean of daily S for comparing the method
        jet_data = xr.DataArray(stj_lat, coords=[self.dates], dims=['time'])
        jet_data_mm = jet_data.resample(freq='MS', dim='time')
        self.jet_lat[hidx, :] = jet_data_mm.values

        jet_data = xr.DataArray(stj_int, coords=[self.dates], dims=['time'])
        jet_data_mm = jet_data.resample(freq='MS', dim='time')
        self.jet_intens[hidx, :] = jet_data_mm.values

        dtimes = [dtime.to_pydatetime() for dtime in jet_data_mm.time.to_index()]
        self.time = date2num(dtimes, self.data.time_units, self.data.calendar)

    def get_jet_loc(self, data, expected_lat, lat):
        """Get jet location based on sign changes of Del(f)."""
        signchange = ((np.roll(np.sign(data), 1) - np.sign(data)) != 0).values
        idx = (np.abs(lat[signchange] - expected_lat)).argmin()

        return lat[signchange][idx]

    def loop_jet_lat(self, data, expected_lat, lat):
        """Get jet location at multiple times."""
        return np.array([self.get_jet_loc(data[tidx, :], expected_lat[tidx], lat)
                         for tidx in range(data.shape[0])])


def lowest_valid(col):
    """Given 1-D array find lowest (along axis) valid data."""
    return col[np.isfinite(col).argmax()]


def get_season(month):
    """Map month index to index of season [DJF -> 0, MAM -> 1, JJA -> 2, SON -> 3]."""
    seasons = np.array([0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3, 0])
    return seasons[month]
