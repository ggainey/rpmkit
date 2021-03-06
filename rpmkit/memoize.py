#
# Copyright (C) 2012, 2013 Satoru SATOH <satoru.satoh@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#


def memoize(fn):
    """memoization decorator.
    """
    assert callable(fn), "Given object is not callable!: " + repr(fn)
    cache = {}

    def wrapped(*args, **kwargs):
        key = repr(args) + repr(kwargs)
        if key not in cache:
            cache[key] = fn(*args, **kwargs)

        return cache[key]

    wrapped.__doc__ = fn.__doc__
    return wrapped

# vim:sw=4:ts=4:et:
