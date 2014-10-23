#
# Copyright (C) 2014 Red Hat, Inc.
# Red Hat Author(s): Satoru SATOH <ssato@redhat.com>
#
# This software is licensed to you under the GNU General Public License,
# version 3 (GPLv3). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. You should have received a copy of GPLv3 along with this
# software; if not, see http://www.gnu.org/licenses/gpl.html
#
import rpmkit.updateinfo.subproc as RUS
import rpmkit.updateinfo.utils as RUU

import os.path
import os
import re
import sys


NAME = "rpmkit.updateinfo.yumwrapper"
ERRATA_REG = re.compile(r"^(?:FEDORA|RH[SBE]A)-")
UPDATE_REG = re.compile(r"^(?P<name>[A-Za-z0-9][^.]+)[.](?P<arch>\w+) +"
                        r"(?:(?P<epoch>\d+):)?(?P<version>[^-]+)-"
                        r"(?P<release>\S+) +(?P<repo>\S+)$")

LOG = RUU.logger_init(NAME)


def _is_errata_line(line, reg=ERRATA_REG):
    """
    >>> ls = [
    ...   "FEDORA-2014-6068 security    cifs-utils-6.3-2.fc20.x86_64",
    ...   "updates/20/x86_64/pkgtags              | 1.0 MB  00:00:03",
    ...   "This system is receiving updates from RHN Classic or RHN ...",
    ...   "RHSA-2013:1732  Low/Sec.    busybox-1:1.15.1-20.el6.x86_64",
    ...   "RHEA-2013:1596  enhancement "
    ...   "ca-certificates-2013.1.94-65.0.el6.noarch",
    ... ]
    >>> _is_errata_line(ls[0])
    True
    >>> _is_errata_line(ls[1])
    False
    >>> _is_errata_line(ls[2])
    False
    >>> _is_errata_line(ls[3])
    True
    >>> _is_errata_line(ls[4])
    True
    """
    return bool(line and reg.match(line))


def _is_update_line(line, reg=ERRATA_REG):
    """
    >>> ls = [
    ...   "FEDORA-2014-6068 security    cifs-utils-6.3-2.fc20.x86_64",
    ...   "updates/20/x86_64/pkgtags              | 1.0 MB  00:00:03",
    ...   "This system is receiving updates from RHN Classic or RHN ...",
    ...   "RHSA-2013:1732  Low/Sec.    busybox-1:1.15.1-20.el6.x86_64",
    ...   "RHEA-2013:1596  enhancement "
    ...   "ca-certificates-2013.1.94-65.0.el6.noarch",
    ... ]
    >>> _is_errata_line(ls[0])
    True
    >>> _is_errata_line(ls[1])
    False
    >>> _is_errata_line(ls[2])
    False
    >>> _is_errata_line(ls[3])
    True
    >>> _is_errata_line(ls[4])
    True
    """
    return bool(line and reg.match(line))


def _parse_errata_type(type_s, sep="/"):
    """
    Parse errata type string in the errata list by 'yum list-sec' or 'yum
    updateinfo list' and detect errata type.

    :param type_s: Errata type string in a line in the errata list
    :return: (errata_type, errata_severity)
        where severity is None if errata_type is not 'Security'.

    >>> _parse_errata_type("Moderate/Sec.")
    ('Security', 'Moderate')
    >>> _parse_errata_type("bugfix")
    ('Bugfix', None)
    >>> _parse_errata_type("enhancement")
    ('Enhancement', None)
    """
    if sep in type_s:
        return ("Security", type_s.split(sep)[0])
    else:
        return (type_s.title(), None)


_RPM_ARCHS = ("i386", "i586", "i686", "x86_64", "ppc", "ia64", "s390",
              "s390x", "noarch")


def _parse_errata_line(line, archs=_RPM_ARCHS, ev_sep=':'):
    """
    Parse a line in the output of 'yum list-sec' or 'yum updateinfo list'.

    See also: The format string '"%(n)s-%(epoch)s%(v)s-%(r)s.%(a)s"' at the
    back of UpdateinfoCommand.doCommand_li in /usr/lib/yum-plugins/security.py

    >>> ls = [
    ...   "RHSA-2013:0587 Moderate/Sec.  openssl-1.0.0-27.el6_4.2.x86_64",
    ...   "RHBA-2013:0781 bugfix         perl-libs-4:5.10.1-131.el6_4.x86_64",
    ...   "RHBA-2013:0781 bugfix         perl-version-3:0.77-131.el6_4.x86_64",
    ...   "RHEA-2013:0615 enhancement    tzdata-2012j-2.el6.noarch",
    ... ]
    >>> xs = [_parse_errata_line(l) for l in ls]

    >>> [(x["advisory"], x["type"],  # doctest: +NORMALIZE_WHITESPACE
    ...   x["severity"]) for x in xs]
    [('RHSA-2013:0587', 'Security', 'Moderate'),
     ('RHBA-2013:0781', 'Bugfix', None),
     ('RHBA-2013:0781', 'Bugfix', None),
     ('RHEA-2013:0615', 'Enhancement', None)]

    >>> [(x["name"], x["epoch"],  # doctest: +NORMALIZE_WHITESPACE
    ...   x["version"], x["release"], x["arch"]) for x in xs]
    [('openssl', '0', '1.0.0', '27.el6_4.2', 'x86_64'),
     ('perl-libs', '4', '5.10.1', '131.el6_4', 'x86_64'),
     ('perl-version', '3', '0.77', '131.el6_4', 'x86_64'),
     ('tzdata', '0', '2012j', '2.el6', 'noarch')]

    """
    (advisory, type_s, pname) = line.rstrip().split()
    (etype, severity) = _parse_errata_type(type_s)

    (rest, arch) = pname.rsplit('.', 1)
    assert arch and arch in archs, \
        "No or invalid arch string found in package name: " + pname

    (name, ev, release) = rest.rsplit('-', 2)

    if ev_sep in ev:
        (epoch, version) = ev.split(ev_sep)
    else:
        epoch = '0'
        version = ev

    url = "https://rhn.redhat.com/errata/%s.html" % advisory.replace(':', '-')

    return dict(advisory=advisory, type=etype, severity=severity,  # Errata
                name=name, epoch=epoch, version=version,  # RPM package
                release=release, arch=arch, url=url)


def _parse_update_line(line, reg=UPDATE_REG):
    """
    Parse a line contains information of update rpm in the output of
    'yum check-update'. If the line is not a line show update rpm, it simply
    returns an empty dict.

    >>> deq = lambda d, tpls: sorted(d.items()) == sorted(tpls)
    >>> s = "bind-libs.x86_64  32:9.8.2-0.17.rc1.el6_4.4  rhel-x86_64-server-6"
    >>> p = _parse_update_line(s)
    >>> p_ref = [("name", "bind-libs"),  # doctest: +NORMALIZE_WHITESPACE
    ...          ("arch", "x86_64"), ("epoch", "32"), ("version", "9.8.2"),
    ...          ("release", "0.17.rc1.el6_4.4"),
    ...          ("repo", "rhel-x86_64-server-6")]
    >>> deq(p, p_ref)
    True

    >>> s = "perl-HTTP-Tiny.noarch   0.017-242.fc18   updates"
    >>> p = _parse_update_line(s)
    >>> p_ref = [("name", "perl-HTTP-Tiny"),  # doctest: +NORMALIZE_WHITESPACE
    ...          ("arch", "noarch"), ("epoch", "0"), ("version", "0.017"),
    ...          ("release", "242.fc18"), ("repo", "updates")]
    >>> deq(p, p_ref)
    True

    >>> s = ""
    >>> _parse_update_line(s)
    {}
    >>> s = "Loaded plugins: downloadonly, product-id, rhnplugin, security"
    >>> _parse_update_line(s)
    {}
    >>> s = "This system is receiving updates from RHN Classic or ..."
    >>> _parse_update_line(s)
    {}
    >>> s = "    usbmuxd.x86_64  1.0.8-10.fc20  @System"  # Obsoletes
    >>> _parse_update_line(s)
    {}
    """
    m = reg.match(line)
    if m:
        p = m.groupdict()
        if p.get("epoch", None) is None:
            p["epoch"] = "0"

        return p
    else:
        return dict()


def logdir(root, log_subdir="var/log"):
    return os.path.join(root, log_subdir)


def run_command(root, command, opts=[], timeout=None, outdir=None):
    """
    Run yum command and get results.

    :param root: Root dir where var/lib/rpm/ exist
    :param command: Yum sub command, ex. 'list-sec'
    :param opts: Extra options for yum, e.g. "--skip-broken ..."
    :param timeout: yum execution timeout
    :param outdir: Logging dir or None
    """
    if root == '/':  # It will refer the system's RPM DB.
        # NOTE: Users except for root cannot make $root/var/log and write logs
        # so try just logging out to stdout and stderr.
        cs = ["yum"] + opts + [command]
        (out, err, rc) = RUS.run(cs, sys.stdout.write, sys.stderr.write,
                                 env={"LANG": "C"})
    else:
        root = os.path.abspath(root)

        if outdir is None:
            outdir = logdir(root)

        if not os.path.exists(outdir):
            os.makedirs(outdir)

        cs = ["yum", "--installroot=%s" % root] + opts + [command]

        command_s = command.replace(' ', '_')
        outpath = os.path.join(outdir, "yum_%s_log.txt" % command_s)
        errpath = os.path.join(outdir, "yum_%s_log.err.txt" % command_s)

        with open(outpath, 'w') as out:
            with open(errpath, 'w') as err:
                (out, err, rc) = RUS.run(cs, out.write, err.write,
                                         timeout, env={"LANG": "C"})

    return (out, err, rc)


def list_errata_g(root, opts=[], timeout=None):
    """
    A generator to return errata found in the output result of 'yum list-sec'
    or 'yum updateinfo list' one by one.

    :param root: Root dir where var/lib/rpm/ exist
    :param opts: Extra options for yum, e.g. "--skip-broken ..."
    :param timeout: yum execution timeout
    """
    (outs, errs, rc) = run_command(root, "list-sec", opts, timeout)

    if rc == 0:
        for line in outs:
            if _is_errata_line(line):
                yield _parse_errata_line(line)
            else:
                LOG.debug("Not errata line: %s" % line.rstrip())
    else:
        LOG.error("Failed to fetch the errata list: %s" % ''.join(errs))


def list_updates_g(root, opts=[], timeout=None):
    """
    A generator to return updates found in the output result of
    'yum check-update'.

    :param root: Root dir where var/lib/rpm/ exist
    :param opts: Extra options for yum, e.g. "--skip-broke ..."
    :param timeout: yum execution timeout
    """
    (outs, errs, rc) = run_command(root, "check-update", opts, timeout)

    # NOTE: 'yum check-update' looks returning non-zero exit code (e.g. 100)
    # when there are any updates found.
    #
    # see also: /usr/share/yum-cli/yummain.py#main
    if rc in (0, 100):
        for line in outs:
            if line:
                line = line.rstrip()
                p = _parse_update_line(line)
                if p:
                    yield p
                else:
                    LOG.debug("Not errata line: %s" % line)
    else:
        LOG.error("Failed to fetch the updates list: %s" % ''.join(errs))


def _mk_repo_opts(repos=[], disabled_repos=[]):
    """
    :note: It must take care of the order of disabled and enabled repos.

    :param repos: A list of enabled repos
    :param disabled_repos: A list of disabled repos

    >>> _mk_repo_opts(['rhel-kstree'], ['*'])
    ["--disablerepo='*'", "--enablerepo='rhel-kstree'"]
    >>> _mk_repo_opts(['rhel-kstree'], [])
    ["--enablerepo='rhel-kstree'"]
    >>> _mk_repo_opts()
    []
    """
    return ["--disablerepo='%s'" % repo for repo in disabled_repos] + \
           ["--enablerepo='%s'" % repo for repo in repos]


def _is_root():
    return os.getuid() == 0


# Wrapper functions of yum commands.
def list_errata(root, repos=[], disabled_repos=['*'], timeout=None):
    """
    Wrapper function of "yum list-sec" / "yum updateinfo list".

    :param root: RPM DB root dir in absolute path
    :param repos: List of Yum repos to enable
    :param disabled_repos: List of Yum repos to disable
    :param timeout: yum execution timeout

    :return: List of dicts of errata info
    """
    return list(list_errata_g(root, _mk_repo_opts(repos, disabled_repos),
                              timeout))


def list_updates(root, repos=[], disabled_repos=['*'], timeout=None):
    """
    Wrapper function of "yum check-update".

    :param root: RPM DB root dir in absolute path
    :param repos: List of Yum repos to enable
    :param disabled_repos: List of Yum repos to disable
    :param timeout: yum execution timeout

    :return: List of dicts of errata info
    """
    return list(p for p in list_updates_g(root,
                                          _mk_repo_opts(repos, disabled_repos),
                                          timeout) if p)


def download_updates(root, repos=[], disabled_repos=['*'], downloaddir=None):
    """
    Wrapper function of "yum --downloadonly --downloaddir=... update ...".

    :param root: RPM DB root dir in absolute path
    :param repos: List of Yum repos to enable
    :param disabled_repos: List of Yum repos to disable
    :param downloaddir: Dir to save downloaded RPMs.
        ``root``/var/cache/.../packages/ will be used if it's None.
    :param timeout: yum execution timeout

    :return: True if success else False
    """
    opts = _mk_repo_opts(repos, disabled_repos)
    opts.append("--skip-broken")

    cs = _is_root() and [] or ["fakeroot"]  # to avoid unneeded check.
    cs.extend(["yum", "--installroot=" + root] + opts +
              ["--downloadonly", "update", "-y"])

    if downloaddir is None:
        # This is not used and does not need to be a real path.
        downloaddir = os.path.join(root, "var/cache/.../<repo_id>/packages/")
    else:
        if not os.path.exists(downloaddir):
            os.makedirs(downloaddir)

        cs.append("--downloaddir=" + downloaddir)

    # TODO: Should these have unique filenames ?
    outdir = logdir(root)
    outpath = os.path.join(outdir, "download_updates_log.txt")
    errpath = os.path.join(outdir, "download_updates_log.err.txt")

    if not os.path.exists(outdir):
        os.makedirs(outdir)

    LOG.info("Update RPMs will be donwloaded under: " + downloaddir)

    with open(outpath, 'w') as out:
        with open(errpath, 'w') as err:
            (_out, err, rc) = RUS.run(cs, out.write, err.write,
                                      env={"LANG": "C"})

    # It seems that 'yum --downloadonly ..' exits with exit code 1 if any
    # downloads found. So we have to take of such cases also.
    if rc == 0:
        LOG.info("No downloads.")
    elif rc == 1:
        LOG.info("Download: OK")
    else:
        LOG.error("Failed to download udpates: " + err)

    return rc in (0, 1)

# vim:sw=4:ts=4:et:
