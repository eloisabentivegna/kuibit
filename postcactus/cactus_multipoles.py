#!/usr/bin/env python3

# Copyright (C) 2020 Gabriele Bozzola, Wolfgang Kastaun
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 3 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, see <https://www.gnu.org/licenses/>.

"""This module provides access to data saved by the multipoles thorn.

   The main class is :py:class:`MultipolesDir`, which is typically
   accessed through :py:class:`~.SimDir` instances.

"""

import os
import re
from functools import lru_cache

import h5py
import numpy as np

from postcactus import timeseries
from postcactus.attr_dict import pythonize_name_dict


class MultipoleOneDet:
    """This class collects multipole components of a specific variable
    a given spherical surface.

    Multipoles are tightly connected with gravitational waves, so morally a
    sphere where multipoles are computed is a "detector". Hence, the name
    of the class.

    It works as a dictionary in terms of the component as a tuple (l,m),
    returning a :py:class:`~.TimeSeries` object. Alternatively, it can be
    called as a function(l,m). Iteration is supported and yields tuples
    (l, m, data), which can be used to loop through all the multipoles
    available.

    The reason we allow for l_min is to remove those files that are not
    necessary when considering gravitational waves (l<2).

    Not intended for direct use.

    :ivar dist: Radius of the sphere
    :vartype dist: float
    :ivar radius: Radius of the sphere
    :vartype radius: float
    :ivar l_min: l smaller than l_min are dropped
    :vartype l_min: int
    :ivar available_l: Available l values
    :vartype available_l: set
    :ivar available_m: Available m values
    :ivar available_m: set
    :ivar available_lm: Available (l, m) values
    :ivar available_lm: set of tuples
    :ivar missing_lm: Missing (l, m) values to have all from l_min to l_max
    :ivar missing_lm: set of tuples

    """

    def __init__(self, dist, data, l_min=0):
        """ Data is a list of tuples with (l, m, timeseries).

        l smaller than l_min are dropped
        """

        self.dist = float(dist)
        self.radius = self.dist  # This is just an alias

        self.l_min = l_min

        # Associate multipoles to list of timeseries
        multipoles_list_ts = {}

        # Now we populate the multipoles_ts_list dictionary. This is a
        # dictionary with keys (l, m) and with values lists of timeseries. If
        # the key is not already present, we create it with value an empty
        # list, then we append the timeseries to this list
        for mult_l, mult_m, ts in data:  # mult = "multipole"
            if (mult_l >= l_min):
                lm_list = multipoles_list_ts.setdefault((mult_l, mult_m), [])
                # This means: multipoles[(mult_t, mult_m)] = []
                lm_list.append(ts)
                # At the end we have:
                # multipoles[(mult_t, mult_m)] = [ts1, ts2, ...]

        # Now self._multipoles is a dictionary in which all the timeseries are
        # collapse in a single one. So it is a straightforward map (l, m) -> ts
        self._multipoles = {lm: timeseries.combine_ts(ts)
                            for lm, ts in multipoles_list_ts.items()}
        self.available_l = sorted({mult_l for mult_l, _
                                   in self._multipoles.keys()})
        self.l_max = max(self.available_l)
        self.available_m = sorted({mult_m for _, mult_m
                                   in self._multipoles.keys()})
        self.available_lm = set(self._multipoles.keys())

        # Check if all the (l, m) from l_min to l_max are available
        all_lm = set()
        for mult_l in range(self.l_min, max(self.available_l) + 1):
            for mult_m in range(-mult_l, mult_l + 1):
                all_lm.add((mult_l, mult_m))

        # set subtraction
        self.missing_lm = all_lm - self.available_lm

        # Data is in the format expected by __init__
        self.data = [(lm[0], lm[1], ts) for lm, ts in self._multipoles.items()]

    def copy(self):
        return type(self)(self.dist, self.data, self.l_min)

    def __contains__(self, key):
        return key in self._multipoles

    def __getitem__(self, key):
        return self._multipoles[key]

    def __call__(self, mult_l, mult_m):
        return self[(mult_l, mult_m)]

    def __eq__(self, other):
        if (not isinstance(other, type(self))):
            return False
        return (self.dist == other.dist and
                self._multipoles == other._multipoles)

    def __iter__(self):
        for (mult_l, mult_m), ts in sorted(self._multipoles.items()):
            yield mult_l, mult_m, ts

    def __len__(self):
        return len(self._multipoles)

    def keys(self):
        return self.available_lm

    def __str__(self):
        ret = f"(l, m) available: {self.keys()}"
        if (self.missing_lm):
            ret += f" (missing: {list(self.missing_lm)})"
        return ret

    def total_function_on_available_lm(self, function, *args, l_max=None,
                                       **kwargs):
        """Evaluate function on each multipole and accumulate the result.

        total_function_on_available_lm will call function with the
        following arguments:

        function(timeseries, mult_l, mult_m, dist, *args, **kwargs)

        If function does not need some paramters, it should use take
        the *args argument to ignore the additional paramters that
        are always passed (l, m, r).

        Values of l larger than l_max are ignored.

        function can take additional paramters passed directly from
        total_function_on_available_lm (e.g. pcut for FFI).

        :params function: Function that has to be applied on each monopole
        :type function: callable

        :returns: Sum of function applied to each monopole
        :rtype: return type of function

        """
        # This function is used to compute many quantities with waves (e.g.,
        # total strain, total power emitted, ...)
        # It is a little bit ugly, but it works
        if (l_max is None):
            l_max = self.l_max

        if (l_max > self.l_max):
            raise ValueError("l max larger than l available")

        # The iterator is increasing in (l, m), so we can start from the first
        # element

        iter_self = iter(self)
        first_l, first_m, first_det = next(iter_self)
        if (first_l > l_max):
            raise ValueError("l max smaller than all l available")
        result = function(first_det, first_l, first_m, self.dist,
                          *args, **kwargs)

        for mult_l, mult_m, det in iter_self:
            if (mult_l <= l_max):
                result += function(det, mult_l, mult_m, self.dist,
                                   *args, **kwargs)

        return result


class MultipoleAllDets:
    """This class collects available surfaces with multipole data.

    It works as a dictionary in terms of spherical surface radius,
    returning a :py:class:`MultipoleOneDet` object. Iteration is supported,
    sorted by ascending radius. You can iterate over all the radii and
    all the available l and m with a nested loop.

    Not intended for direct use.

    :ivar radii:        Available surface radii.
    :vartype radii: float
    :ivar r_outer:      Radius of the outermost detector
    :vartype r_outer: float
    :ivar l_min: l smaller than l_min are dropped
    :vartype l_min: int
    :ivar outermost:    Outermost detector
    :vartype outermost: :py:class:`~MultipoleOneDet`
    :ivar available_lm: Available components as tuple (l,m).
    :vartype available_lm: list of tuples
    :ivar available_l:  List of available "l".
    :vartype available_l: list
    :ivar available_m:  List of available "m".
    :vartype available_m: list

    """

    def __init__(self, data, l_min=0):
        """Data is a list of tuples with structure
        (multipole_l, multipole_m, extraction_radius, [timeseries])
        """
        self.l_min = l_min

        detectors = {}
        self.available_lm = set()

        # Populate detectors
        # detectors is a dictionary with keys the radii and
        # items that look like ([l, m, timeseries, ...]).
        # We accumulate in the list all the ones for the same
        # radius
        for mult_l, mult_m, radius, ts in data:
            if (mult_l >= self.l_min):
                # If we don't have the radius yet, let's create an empty list
                d = detectors.setdefault(radius, [])
                # Add the new values
                d.append((mult_l, mult_m, ts))
                # Tally the available l and m
                self.available_lm.add((mult_l, mult_m))

        self._detectors = {radius: MultipoleOneDet(radius, multipoles,
                                                   self.l_min)
                           for radius, multipoles in detectors.items()}

        # In Python3 .keys() is not a list
        self.radii = sorted(list(self._detectors.keys()))

        if len(self.radii) > 0:
            self.r_outer = self.radii[-1]
            self.outermost = self._detectors[self.r_outer]
        #
        self.available_l = sorted({mult_l for mult_l, _ in self.available_lm})
        self.l_max = max(self.available_l)
        self.available_m = sorted({mult_m for _, mult_m in self.available_lm})

        # Alias
        self._dets = self._detectors

        # Data is in the format expected by __init__
        # (multipole_l, multipole_m, extraction_radius, [timeseries])
        self.data = []
        for radius, det in self._dets.items():
            for mult_l, mult_m, ts in det:
                self.data.append((mult_l, mult_m, radius, ts))

    def copy(self):
        return type(self)(self.data, self.l_min)

    def has_detector(self, mult_l, mult_m, dist):
        """Check if a given multipole component extracted at a given
        distance is available.

        :param mult_l:     Multipole component mult_l
        :type mult_l:      int
        :param mult_m:     Multipole component m
        :type mult_m:      int
        :param dist:  Distance of detector
        :type dist:   float

        :returns:     If available or not
        :rtype:       bool
        """
        if dist in self:
            return (mult_l, mult_m) in self[dist]
        return False

    def __contains__(self, key):
        return key in self._dets

    def __getitem__(self, key):
        return self._dets[key]

    def __iter__(self):
        for r in self.radii:
            yield self[r]

    def __eq__(self, other):
        if (not isinstance(other, type(self))):
            return False
        return (self.radii == other.radii and
                self._dets == other._dets)

    def __len__(self):
        return len(self._dets)

    def keys(self):
        # TODO: In Python3 keys are not list, but iterables
        return list(self._dets.keys())

    def __str__(self):
        ret = f"Radii avilable: {self.keys()}\n\n"
        for d in sorted(self.keys()):
            ret += f"At radius {d}, {self._dets[d]}\n"
        return ret


class MultipolesDir:
    """This class provides acces to various types of multipole data in a given
    simulation directory.

    This class is like a dictionary, you can access its values using the
    brackets operator. The output is a :py:mod:`~.MultipoleAllDets`, which has
    a full multipolar description for all the available radii. Files are lazily
    loaded. If both h5 and ASCII are present, h5 are preferred. There's no
    attempt to combine the two.

    Alternatively, you can access variables with get() or with fields.var_name.
    """

    def __init__(self, sd):
        # self._vars is a dictionary. For _vars_txt, the keys are the
        # variables  and the items are lists of tuples of the form
        # (multipole_l,  multipole_m, radius, filename) for text files.
        #
        # For _vars_h5 they are lists (since we have to read the content
        # to find the l and m)
        self._vars_txt = {}
        self._vars_h5 = {}

        self.path = sd.path
        # First, we need to find the multipole files.
        # There are text files and h5 files
        #
        # We use a regular expressions on the name
        # The structure is like mp_Psi4_l_m2_r110.69.asc
        #
        # Let's understand the regexp:
        # 0. ^ and $ means that we match the entire name
        # 1. We match mp_ followed my the variable name, which is
        #    any combination of characters
        # 2. We match _l with a number
        # 3. We match _m with possibly a minus sign and a number
        # 4. We match _r with any combination of numbers with possibly
        #    dots
        # 5. We possibly match a compression
        rx_txt = re.compile(r"""^
        mp_([a-zA-Z0-9\[\]_]+)
        _l(\d+)
        _m([-]?\d+)
        _r([0-9.]+)
        .asc
        (?:.bz2|.gz)?
        $""", re.VERBOSE)

        # For h5 files is easy: it is just the var name
        rx_h5 = re.compile(r'^mp_([a-zA-Z0-9\[\]_]+).h5$')

        for f in sd.allfiles:
            filename = os.path.split(f)[1]
            matched_h5 = rx_h5.match(filename)
            matched_txt = rx_txt.match(filename)
            if (matched_h5 is not None):
                variable_name = matched_h5.group(1).lower()
                var_list = self._vars_h5.setdefault(variable_name, [])
                # We are flagging that this h5
                var_list.append(f)
            elif (matched_txt is not None):
                variable_name = matched_txt.group(1).lower()
                mult_l = int(matched_txt.group(2))
                mult_m = int(matched_txt.group(3))
                radius = float(matched_txt.group(4))
                var_list = self._vars_txt.setdefault(variable_name, [])
                var_list.append((mult_l, mult_m, radius, f))

        # What pythonize_name_dict does is to make the various variables
        # accessible as attributes, e.g. self.fields.rho
        self.fields = pythonize_name_dict(list(self.keys()),
                                          self.__getitem__)

    def __contains__(self, key):
        return str(key).lower() in self.keys()

    # The following are staticmethods because they do not depend on the bound
    # object. Using this decorator we save memory because Python will
    # initialize them only once.

    @staticmethod
    def _multipole_from_textfile(path):
        a = np.loadtxt(path, unpack=True, ndmin=2)
        if (len(a) != 3):
            raise RuntimeError(f'Wrong format in {path}')
        complex_mp = a[1] + 1j * a[2]
        return timeseries.remove_duplicate_iters(a[0],
                                                 complex_mp)

    @staticmethod
    def _multipoles_from_h5file(path):
        alldets = []
        # This regexp matches : l(number)_m(-number)_r(number)
        fieldname_pattern = re.compile(r'l(\d+)_m([-]?\d+)_r([0-9.]+)')

        with h5py.File(path, 'r') as data:

            # Loop over the groups in the hdf5
            for entry in data.keys():
                matched = fieldname_pattern.match(entry)
                if matched:
                    mult_l = int(matched.group(1))
                    mult_m = int(matched.group(2))
                    radius = float(matched.group(3))
                    # Read the actual data
                    a = data[entry][()].T
                    ts = timeseries.TimeSeries(a[0],
                                               a[1] + 1j*a[2])
                    alldets.append((mult_l, mult_m,
                                    radius, ts))

        return alldets

    def _multipoles_from_textfiles(self, mpfiles):
        # We prepare the data for MultipoleAllDets checking
        # for errors
        alldets = [(mult_l, mult_m, radius,
                    self._multipole_from_textfile(filename)) for
                   mult_l, mult_m, radius, filename in mpfiles]
        return MultipoleAllDets(alldets)

    def _multipoles_from_h5files(self, mpfiles):
        mult_l = []

        for filename in mpfiles:
            mult_l.extend(self._multipoles_from_h5file(filename))

        return MultipoleAllDets(mult_l)

    @lru_cache(128)
    def __getitem__(self, key):
        """:The return value is py:class:`~.MultipoleAllDets`.
        """
        k = str(key).lower()
        # We prefer h5
        if k in self._vars_h5:
            return self._multipoles_from_h5files(self._vars_h5[k])

        if k in self._vars_txt:
            return self._multipoles_from_textfiles(self._vars_txt[k])

        raise KeyError

    def get(self, key, default=None):
        if key not in self:
            return default
        return self[key]

    def keys(self):
        # To find the unique keys we use transofrm the keys in sets, and then
        # we unite them.
        return list(
            set(self._vars_h5.keys()).union(set(self._vars_txt.keys())))

    def __str__(self):
        """NOTE: __str__ requires opening all the h5 files!
        This can be slow!
        """
        ret = f"Folder: {self.path}\n"
        ret += f"Variables available: {self.keys()}\n"
        for variable in self.keys():
            ret += f"For variable {variable}:\n\n"
            ret += f"{self[variable]}\n"
        return ret
