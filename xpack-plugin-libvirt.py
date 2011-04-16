#
# xpack plugin for libvirt objects:
# 
# Copyright (C) 2011 Satoru SATOH <satoru.satoh @ gmail.com>
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
#
# Requirements: xpack
#
#
# Installation: ...
#
# References:
# * http://libvirt.org/html/libvirt-libvirt.html
# * http://libvirt.org/formatdomain.html
# * http://libvirt.org/formatnetwork.html
#
#
# TODO:
# * plugin template and basic mechanism to overwrite parameters from main
# * VMs other than KVM guests such like LXC guests
#
#
# Internal:
#
# Make some pylint errors ignored:
# pylint: disable=E0611
# pylint: disable=E1101
# pylint: disable=E1103
# pylint: disable=W0613
#
# How to run pylint: pylint --rcfile pylintrc THIS_SCRIPT
#

import copy
import doctest
import glob
import itertools
import libvirt
import libxml2
import logging
import re
import unittest
import xpack



VMM = "qemu:///system"



def xml_context(xmlfile):
    return libxml2.parseFile(xmlfile).xpathNewContext()


def xpath_eval(xpath, xmlfile=False, ctx=False):
    """Parse given XML and evaluate given XPath expression, then returns
    result[s].
    """
    assert xmlfile or ctx, "No sufficient arguements"

    if not ctx:
        ctx = xml_context(xmlfile)

    return [r.content for r in ctx.xpathEval(xpath)]



class LibvirtObject(object):

    def __init__(self, name=False, xmlpath=False, vmm=VMM):
        assert name or xmlpath, "Not enough arguments"

        self.vmm = vmm
        self.type = self.type_by_vmm(vmm)

        if name:
            self.name = name
            self.xmlpath = self.xmlpath_by_name(name)
        else:
            self.xmlpath = xmlpath
            self.name = self.name_by_xml_path(xmlpath)

    def xpath_eval(self, xpath):
        return xpath_eval(xpath, self.xmlpath)

    def name_by_xmlpath(self, xmlpath):
        return self.xpath_eval("//name", xmlpath)[0]

    def xmlpath_by_name(self, name):
        return "/etc/libvirt/%s/%s.xml" % (self.type, name)

    def type_by_vmm(self, vmm):
        return vmm.split(":")[0]  # e.g. 'qemu'

    def connect(self):
        return libvirt.openReadOnly(self.vmm)



class LibvirtNetwork(LibvirtObject):

    def name_by_xmlpath(self, xmlpath):
        return self.xpath_eval('/network/name', xmlpath)[0]

    def xmlpath_by_name(self, name):
        return "/etc/libvirt/%s/networks/%s.xml" % (self.type, name)



class LibvirtDomain(LibvirtObject):

    def name_by_xmlpath(self, xmlpath):
        return self.xpath_eval('/domain/name', xmlpath)[0]

    def parse(self):
        """Parse domain xml and store various guest profile data.

        TODO: storage pool support
        """
        self.arch = self.xpath_eval('/domain/os/type/@arch')[0]
        self.networks = xpack.unique(self.xpath_eval('/domain/devices/interface[@type="network"]/source/@network'))

        images = self.xpath_eval('/domain/devices/disk[@type="file"]/source/@file')
        dbs = [(img, self.get_base_image_path(img)) for img in images]
        self.base_images = [db[1] for db in dbs if db[1]] + [db[0] for db in dbs if not db[1]]
        self.delta_images = [db[0] for db in dbs if db[1]]

    def status(self):
        conn = self.connect()

        if conn is None: # libvirtd is not running.
            return libvirt.VIR_DOMAIN_SHUTOFF

        dom = conn.lookupByName(self.name)
        if dom:
            return dom.info()[0]
        else:
            return libvirt.VIR_DOMAIN_NONE

    def is_running(self):
        return self.status() == libvirt.VIR_DOMAIN_RUNNING

    def is_shutoff(self):
        return self.status() == libvirt.VIR_DOMAIN_SHUTOFF

    def get_base_image_path(self, image_path):
        try:
            out = subprocess.check_output("qemu-img info %s" % image_path, shell=True)
            m = re.match(r"^backing file: (.+) \(actual path: (.+)\)$", out.split("\n")[-2])
            if m:
                (delta, base) = m.groups()
                return base
            else:
                return False
        except Exception, e:
            logging.warn("get_delta_image_path: " + str(e))
            pass



# plugin main:
__version__ = "0.1"
__author__  = "Satoru SATOH"
__email__   = "satoru.satoh@gmail.com"
__website__ = "https://github.com/ssato/rpmkit"


PKGDVM_TEMPLATES = copy.copy(xpack.TEMPLATES)
PKGDVM_TEMPLATES.update(
{
    "scriptlets": """\
%preun
if [ \$1 = 0 ]; then  # erase
    if `/usr/bin/virsh list | grep -q ${vm.name} 2>/dev/null`; then
        echo "${vm.name} is still running and cannot be uninstalled right now. Please stop it and try again later."
        exit 1
    else
        /usr/bin/virsh undefine ${vm.name}
    fi
fi

%post
if [ \$1 = 1 ]; then    # install
    /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml
elif [ \$1 = 2 ]; then  # update
    if `/usr/bin/virsh list | grep -q ${vm.name} 2>/dev/null`; then
        echo "${vm.name} is running. Run the following later when it's stopped to update its profile:"
        echo "   /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml"
    else
        /usr/bin/virsh undefine ${vm.name}
        /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml
    fi
fi
""",
    "debian/postinst": """\
#!/bin/sh
#
# see: dh_installdeb(1)
#
# summary of how this script can be called:
#        * <postinst> `configure' <most-recently-configured-version>
#        * <old-postinst> `abort-upgrade' <new version>
#        * <conflictor's-postinst> `abort-remove' `in-favour' <package>
#          <new-version>
#        * <postinst> `abort-remove'
#        * <deconfigured's-postinst> `abort-deconfigure' `in-favour'
#          <failed-install-package> <version> `removing'
#          <conflicting-package> <version>
# for details, see http://www.debian.org/doc/debian-policy/ or
# the debian-policy package

set -e

case "\$1" in
    configure)
        if `/usr/bin/virsh list --all | grep -q ${vm.name} 2>/dev/null`; then
            if `/usr/bin/virsh list | grep -q ${vm.name} 2>/dev/null`; then
                echo "${vm.name} is running. Run the following later when it's stopped:"
                echo "   /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml"
            else
                /usr/bin/virsh undefine ${vm.name}
                /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml
            fi
        else
            /usr/bin/virsh define ${vm.xmlsavedir}/${vm.name}.xml
        fi
    ;;

    abort-upgrade|abort-remove|abort-deconfigure)
    ;;

    *)
        echo "postinst called with unknown argument \`\$1'" >&2
        exit 1
    ;;
esac

#DEBHELPER#

exit 0
""",
    "debian/postinst": """\
#!/bin/sh
#
# see: dh_installdeb(1)
set -e

case "\$1" in
    remove|upgrade|deconfigure)
        if `/usr/bin/virsh list | grep -q ${vm.name} 2>/dev/null`; then
            echo "${vm.name} is still running and cannot be uninstalled right now. Please stop it and try again later."
            exit 1
        else
            /usr/bin/virsh undefine ${vm.name}
        fi
        ;;
    failed-upgrade)
        ;;
    *)
        echo "prerm called with unknown argument \`\$1'" >&2
        exit 0
        ;;
esac

#DEBHELPER#

exit 0
""",
})



# PackageMaker Inherited classes:
class RpmLibvirtDomainPackageMaker(xpack.RpmPackageMaker):

    global PKGDVM_TEMPLATES

    _templates = PKGDVM_TEMPLATES
    _type = "libvirt.domain"
    _format = "rpm"

    def __init__(self, package, vmname, options, *args, **kwargs):
        self.domain = LibvirtDomain(vmname)
        self.domain.parse()

        filelist = [self.domain.xmlpath]
        filelist += self.domain.base_images
        filelist += self.domain.delta_images

        super(RpmLibvirtDomainPackageMaker, self).__init__(package, filelist, options)

        self.package["scriptlets"] = self.templates().get("scriptlets", "")



class DebLibvirtDomainPackageMaker(xpack.DebPackageMaker):

    global PKGDVM_TEMPLATES

    _templates = PKGDVM_TEMPLATES
    _type = "libvirt.domain"
    _format = "deb"



RpmLibvirtDomainPackageMaker.register()
DebLibvirtDomainPackageMaker.register()

# vim:sw=4:ts=4:et: