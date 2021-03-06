#
# -*- coding: utf-8 -*-
#
# extend rpmkit.updateinfo.main for multiple host analysis.
#
# Copyright (C) 2013, 2014 Satoru SATOH <ssato@redhat.com>
# Copyright (C) 2014 Red Hat, Inc.
# License: GPLv3+
#
from rpmkit.globals import _

import rpmkit.updateinfo.main as RUM
import rpmkit.updateinfo.utils
import rpmkit.utils as U

# It looks available in EPEL for RHELs:
#   https://apps.fedoraproject.org/packages/python-bunch
import bunch
import glob
import itertools
import logging
import multiprocessing
import operator
import os
import os.path
import shutil


LOG = logging.getLogger("rpmkit.updateinfo")


def hosts_rpmroot_g(hosts_datadir):
    """
    List system names from assessment datadir.

    This function expects that assessment data (rpm db files) of each hosts are
    found under $host_identity/ in `datadir`, that is,
    `datadir`/<host_identity>/var/lib/rpm/Packages exists. If rpm db file[s]
    are not found for a host, that host will be simply ignored.

    <host_identity> may be a hostname, host id, fqdn or something to
    identify that host.

    :param hosts_datadir: Dir in which rpm db roots of hosts exist
    :return: A generator to yield a tuple,
        (host_identity, host_rpmroot or None)
    """
    for hostdir in glob.glob(os.path.join(hosts_datadir, '*')):
        if rpmkit.updateinfo.utils.check_rpmdb_root(hostdir):
            yield (os.path.basename(hostdir), hostdir)
        else:
            LOG.warn(_("Failed to find RPM DBs under %s"), hostdir)
            yield (os.path.basename(hostdir), None)


def touch(filepath):
    open(filepath, 'w').write()


def prepare(hosts_datadir, workdir=None, repos=[], cachedir=None,
            backend=RUM.DEFAULT_BACKEND, backends=RUM.BACKENDS):
    """
    Scan and collect hosts' basic data (installed rpms list, etc.).

    :param hosts_datadir: Dir in which rpm db roots of hosts exist
    :param workdir: Working dir to save results
    :param repos: List of yum repos to get updateinfo data (errata and updtes)
    :param cachedir: A dir to save metadata cache of yum repos
    :param backend: Backend module to use to get updates and errata
    :param backends: Backend list

    :return: A generator to yield a tuple,
        (host_identity, host_rpmroot or None)
    """
    if workdir is None:
        LOG.info(_("Set workdir to hosts_datadir: %s"), hosts_datadir)
        workdir = hosts_datadir
    else:
        if not os.path.exists(workdir):
            LOG.debug(_("Creating working dir: %s"), workdir)
            os.makedirs(workdir)

    for h, root in hosts_rpmroot_g(hosts_datadir):
        hworkdir = os.path.join(workdir, h)
        if not hworkdir:
            os.makedirs(hworkdir)

        if root is None:
            touch(os.path.join(hworkdir, "RPMDB_NOT_AVAILABLE"))
            yield bunch.bunchify(dict(id=h, workdir=hworkdir, available=False))
        else:
            yield RUM.prepare(root, hworkdir, repos, h, cachedir, backend,
                              backends)


def p2nevra(p):
    """
    :param p: A dict represents package info including N, E, V, R, A
    """
    return operator.itemgetter("name", "epoch", "version", "release",
                               "arch")(p)


def add_host_to_metadata(workdir, host):
    metadatafile = os.path.join(workdir, "metadata.json")

    shutil.copy2(metadatafile, metadatafile + ".save")
    metadata = U.json_load(metadatafile)
    metadata["hosts"].append(host)
    U.json_dump(metadata, metadatafile)


def mk_symlinks_to_results_of_ref_host(href, hsrest, curdir=os.curdir):
    """
    :param href: Reference host object
    :param hsrest: A list of hosts having same installed rpms as `href`
    :param curdir: Current dir to go back

    TODO: Ugly code around symlinks ...
    """
    for h in hsrest:
        os.chdir(h.workdir)
        href_workdir = os.path.join('..', href.id)  # TODO: Keep consistency.
        LOG.info(_("%s: Make symlinks to results in %s/"), h.id, href_workdir)
        for src in glob.glob(os.path.join(href_workdir, '*.*')):
            dst = os.path.basename(src)
            if not os.path.exists(dst):
                LOG.debug("Make a symlink to %s", src)
                os.symlink(src, dst)

        add_host_to_metadata(href_workdir, h.id)
        os.chdir(curdir)


def analyze(args):
    RUM.analyze(*args)


def main(hosts_datadir, workdir=None, repos=[], score=-1,
         keywords=RUM.ERRATA_KEYWORDS, rpms=[], period=(), cachedir=None,
         refdir=None, verbosity=0, multiproc=False,
         backend=RUM.DEFAULT_BACKEND, backends=RUM.BACKENDS):
    """
    :param hosts_datadir: Dir in which rpm db roots of hosts exist
    :param workdir: Working dir to save results
    :param repos: List of yum repos to get updateinfo data (errata and updtes)
    :param score: CVSS base metrics score
    :param keywords: Keyword list to filter 'important' RHBAs
    :param rpms: Core RPMs to filter errata by them
    :param period: Period of errata in format of YYYY[-MM[-DD]],
        ex. ("2014-10-01", "2014-11-01")
    :param cachedir: A dir to save metadata cache of yum repos
    :param refdir: A dir holding reference data previously generated to
        compute delta (updates since that data)
    :param verbosity: Verbosity level: 0 (default), 1 (verbose), 2 (debug)
    :param multiproc: Utilize multiprocessing module to compute results
        in parallel as much as possible if True
    :param backend: Backend module to use to get updates and errata
    :param backends: Backend list
    """
    RUM.set_loglevel(verbosity)

    all_hosts = list(prepare(hosts_datadir, workdir, repos, cachedir, backend,
                             backends))
    hosts = [h for h in all_hosts if h.available]

    LOG.info(_("Analyze %d/%d hosts"), len(hosts), len(all_hosts))

    ilen = lambda h: len(h.installed)
    hps = lambda h: [p2nevra(p) for p in h.installed]
    gby = lambda xs, kf: itertools.groupby(sorted(xs, key=kf), kf)

    # Group hosts by installed rpms to degenerate these hosts and avoid to
    # analyze for same installed RPMs more than once. his :: [[[h]]]
    his = [[list(g2) for _k2, g2 in gby(g, hps)] for _k, g in gby(hosts, ilen)]

    for hss in his:
        hset = [(hs[0], hs[1:]) for hs in hss]
        hsdata = [(h, score, keywords, rpms, period, refdir) for h, _hrest
                  in hset]

        if multiproc:
            pool = multiprocessing.Pool(multiprocessing.cpu_count())
            pool.map(analyze, hsdata)
        else:
            for args in hsdata:
                analyze(args)

        for h, hsrest in hset:
            if hsrest:
                LOG.info(_("Skip to analyze %s as its installed RPMs are "
                           "exactly same as %s's"),
                         ','.join(x.id for x in hsrest), h)
                mk_symlinks_to_results_of_ref_host(h, hsrest, os.curdir)

# vim:sw=4:ts=4:et:
