#
# Make caches of various yum 'list' command execution results.
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
"""Make caches of various yum 'list' command execution results.

Usage:
    su - apache -c 'yum_makelistcache [Options ...] ...'
"""
import codecs
import commands
import datetime
import glob
import logging
import operator
import optparse
import os.path
import os
import re
import sys
import yum

try:
    import ConfigParser as configparser
except ImportError:
    import configparser

try:
    import bsddb
except ImportError:
    bsddb = None

try:
    import json
except ImportError:
    import simplejson as json

try:
    _MODE_RO = eval('0o444')
except SyntaxError:  # Older python (2.4.x in RHEL 5) doesn't like the above.
    _MODE_RO = eval('0444')

try:
    all
except NameError:
    def all(xs):
        for x in xs:
            if not x:
                return False
        return True


NAME = "yum_makelistcache"

logging.basicConfig(format="%(asctime)-15s [%(levelname)s] %(message)s")

LOG = logging.getLogger(NAME)

_RPM_DB_FILENAMES = ["Basenames", "Name", "Providename", "Requirename"]
_RPM_KEYS = ("nevra", "name", "epoch", "version", "release", "arch")


class JST(datetime.tzinfo):
    def utcoffset(self, *args):
        return datetime.timedelta(hours=9)

    def dst(self, *args):
        return datetime.timedelta(0)

    def tzname(self, *args):
        return "JST"


def _localtime(tz=JST()):
    return datetime.datetime.now(tz).strftime("%c %Z")


def _open(path, flag='r', encoding="utf-8"):
    return codecs.open(path, flag, encoding)


def _is_bsd_hashdb(dbpath):
    """
    FIXME: Is this enough to check if given file ``dbpath`` is RPM DB file ?
    """
    return True

    try:
        if bsddb is None:
            return True  # bsddb is not avialable in python3.

        bsddb.hashopen(dbpath, 'r')
    except:
        return False

    return True


def _rpmdb_files_exist(path, rpmdb_filenames=_RPM_DB_FILENAMES):
    """
    :param path: RPM DB path
    """
    return all(os.path.exists(os.path.join(path, f)) for f in rpmdb_filenames)


def logpath(root, basename):
    return os.path.join(root, "var/log", basename)


def setup_root(root, readonly=True):
    """
    :param root: The pivot root directry where target's RPM DB files exist.
    :param readonly: Ensure RPM DB files readonly.
    :return: True if necessary setup was done w/ success else False
    """
    assert root != "/",  "Do not run this for host system's RPM DB!"

    rpmdbdir = os.path.join(root, "var/lib/rpm")

    if not os.path.exists(rpmdbdir):
        LOG.error("RPM DB dir %s does not exist!" % rpmdbdir)
        return False

    pkgdb = os.path.join(rpmdbdir, "Packages")
    if not _is_bsd_hashdb(pkgdb):
        LOG.error("%s does not look a RPM DB (Packages) file!" % pkgdb)
        return False

    if not _rpmdb_files_exist(rpmdbdir):
        LOG.error("Some RPM DB files look missing! Check it.")
        return False

    if readonly:
        fs = [f for f in glob.glob(os.path.join(rpmdbdir, "[A-Z]*"))
              if os.access(f, os.W_OK)]

        LOG.info("Drop write access perm: " + ', '.join(fs))
        for f in fs:
            if os.access(f, os.W_OK):
                os.chmod(f, _MODE_RO)

    logdir = os.path.dirname(logpath(root, "list.log"))
    if not os.path.exists(logdir):
        os.makedirs(logdir)

    return True


def noop(*args, **kwargs):
    pass


def _toggle_repos(base, repos_to_act, act="enable"):
    for repo_match in repos_to_act:
        for repo in base.repos.findRepos(repo_match):
            getattr(repo, act, noop)()


def _activate_repos(base, enablerepos=[], disablerepos=['*']):
    _toggle_repos(base, disablerepos, "disable")
    _toggle_repos(base, enablerepos, "enable")


def _find_valid_attrs_g(obj, attrs=[]):
    for a in attrs:
        try:
            if getattr(obj, a, None) is not None:
                yield a
        except (AttributeError, KeyError):
            LOG.debug("Attr '%s' is missing in the obj" % a)
            pass


_PKG_NARROWS = ("installed", "updates", "obsoletes")


def yum_list(root, enablerepos=[], disablerepos=['*'],
             pkgnarrows=_PKG_NARROWS):
    """
    List installed or update RPMs similar to
    "repoquery --pkgnarrow=updates --all --plugins --qf '%{nevra}'".

    :param root: RPM DB root dir in absolute path
    :param enablerepos: List of Yum repos to enable
    :param disablerepos: List of Yum repos to disable
    :param pkgnarrows: List of types to narrrow packages list

    :return: A dict contains lists of dicts of packages
    """
    base = yum.YumBase()

    try:
        base.preconf.root = root
    except:
        base.conf.installroot = root

    base.logger = base.verbose_logger = LOG
    _activate_repos(base, enablerepos, disablerepos)

    if pkgnarrows != ("installed", ):
        base.repos.populateSack()

    ret = dict()

    for pn in pkgnarrows:
        ygh = base.doPackageLists(pn)
        ret[pn] = getattr(ygh, pn)

    return ret


def _pkgs2dicts(pkgs, keys=_RPM_KEYS):
    if not pkgs:
        return []

    keys = list(_find_valid_attrs_g(pkgs[0], keys))
    return [dict((k, getattr(p, k, None)) for k in keys) for p in pkgs]


def _reg_by_dist(dist="rhel"):
    if dist == "fedora":
        reg = r"^FEDORA-"
    else:
        reg = r"^RH[SBE]A-"

    return re.compile(reg)


_RH_ERRATA_REG = _reg_by_dist("rhel")


def _is_errata_line(line, reg=_RH_ERRATA_REG):
    """
    >>> ls = [
    ...   "FEDORA-2014-6068 security    cifs-utils-6.3-2.fc20.x86_64",
    ...   "updates/20/x86_64/pkgtags              | 1.0 MB  00:00:03",
    ...   "This system is receiving updates from RHN Classic or RHN ...",
    ...   "RHSA-2013:1732  Low/Sec.    busybox-1:1.15.1-20.el6.x86_64",
    ...   "RHEA-2013:1596  enhancement "
    ...   "ca-certificates-2013.1.94-65.0.el6.noarch",
    ... ]
    >>> _is_errata_line(ls[0], _reg_by_dist("fedora"))
    True
    >>> _is_errata_line(ls[1], _reg_by_dist("fedora"))
    False
    >>> _is_errata_line(ls[2])
    False
    >>> _is_errata_line(ls[3])
    True
    >>> _is_errata_line(ls[4])
    True
    """
    return bool(line and reg.match(line))


def __parse_errata_type(type_s, sep="/"):
    """
    Parse errata type string in the errata list by 'yum list-sec'.

    :param type_s: Errata type string in the errata list
    :return: (errata_type, errata_severity)
        where severity is None if errata_type is not 'Security'.

    >>> __parse_errata_type("Moderate/Sec.")
    ('Security', 'Moderate')
    >>> __parse_errata_type("bugfix")
    ('Bugfix', None)
    >>> __parse_errata_type("enhancement")
    ('Enhancement', None)
    """
    if sep in type_s:
        return ("Security", type_s.split(sep)[0])
    else:
        return (type_s.title(), None)


_RPM_ARCHS = ("i386", "i586", "i686", "x86_64", "ppc", "ia64", "s390",
              "s390x", "noarch")


def parse_errata_line(line, archs=_RPM_ARCHS, ev_sep=':'):
    """
    Parse a line in the output of 'yum list-sec'.

    See also: The format string '"%(n)s-%(epoch)s%(v)s-%(r)s.%(a)s"' at the
    back of UpdateinfoCommand.doCommand_li in /usr/lib/yum-plugins/security.py

    >>> ls = [
    ...   "RHSA-2013:0587 Moderate/Sec.  openssl-1.0.0-27.el6_4.2.x86_64",
    ...   "RHBA-2013:0781 bugfix         perl-libs-4:5.10.1-131.el6_4.x86_64",
    ...   "RHBA-2013:0781 bugfix         perl-version-3:0.77-131.el6_4.x86_64",
    ...   "RHEA-2013:0615 enhancement    tzdata-2012j-2.el6.noarch",
    ... ]
    >>> xs = [parse_errata_line(l) for l in ls]

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
    (etype, severity) = __parse_errata_type(type_s)

    (rest, arch) = pname.rsplit('.', 1)
    assert arch and arch in archs, \
        "no or invalid arch string found in package name: " + pname

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


def _run(cmd, output=None, curdir=os.curdir):
    cmd_s = ' '.join(cmd)
    LOG.info("Run '%s' in %s" % (cmd_s, curdir))
    (rc, out) = commands.getstatusoutput("cd %s && %s" % (curdir, cmd_s))

    if output:
        f = open(output, 'w')
        f.write(out)
        f.close()

    return (rc, rc == 0 and '' or out)


def list_errata_g(root, opts=[], dist=None):
    """
    A generator to return errata found in the output result of 'yum list-sec'
    one by one.

    :param root: Pivot root dir where var/lib/rpm/ exist.
    :param opts: Extra options for yum, e.g. "--enablerepo='...' ..."
    :param dist: Distribution name or None
    """
    cs = ["yum", "--installroot=" + root] + opts + ["list-sec"]
    output = logpath(root, "yum_list-sec.log")
    (rc, err) = _run(cs, output)

    if rc == 0:
        lines = open(output).readlines()
        reg = _reg_by_dist()

        for line in lines:
            line = line.rstrip()
            if _is_errata_line(line, reg):
                # LOG.debug("Errata line: " + line)
                yield parse_errata_line(line)
            else:
                LOG.debug("Not errata line: " + line)
    else:
        LOG.error("Failed to fetch the errata list: " + err)


def _mk_repo_opts(enablerepos, disablerepos):
    """
    :note: Take care of the order of disabled and enabled repos.
    """
    LOG.info("disabled=%s, enabled=%s" % (','.join(disablerepos),
                                          ','.join(enablerepos)))
    return ["--disablerepo='%s'" % repo for repo in disablerepos] + \
           ["--enablerepo='%s'" % repo for repo in enablerepos]


def yum_list_errata(root, enablerepos=[], disablerepos=['*']):
    """
    List errata similar to "yum list-sec".

    :param root: RPM DB root dir in absolute path
    :param enablerepos: List of Yum repos to enable
    :param disablerepos: List of Yum repos to disable

    :return: List of dicts contain each errata info
    """
    opts = _mk_repo_opts(enablerepos, disablerepos)
    return list(list_errata_g(root, opts))


def _is_root():
    return os.getuid() == 0


def yum_download(root, enablerepos=[], disablerepos=['*'], outdir=None):
    """
    Download update RPMs.

    :param root: RPM DB root dir in absolute path
    :param enablerepos: List of Yum repos to enable
    :param disablerepos: List of Yum repos to disable
    :param outdir: Output dir. ``root``/var/cache/.../packages/ will be used
        if it's None.

    :return: List of dicts contain each errata info
    yum update -y --downloadonly
    """
    opts = _mk_repo_opts(enablerepos, disablerepos)
    opts.append("--skip-broken")

    cs = _is_root() and [] or ["fakeroot"]  # avoid unneeded check.
    cs += ["yum", "--installroot=" + root] + opts + ["--downloadonly",
                                                     "update", "-y"]

    if outdir:
        if not os.path.exists(outdir):
            os.makedirs(outdir)

        cs += ["--downloaddir=" + outdir]
    else:
        # This is not used and just appears in log messages actually:
        outdir = os.path.join(root, "var/cache/.../<repo_id>/packages/")

    output = logpath(root, "yum_download.log")
    LOG.info("Update RPMs will be donwloaded under: " + outdir)

    (rc, err) = _run(cs, output)

    # It seems that 'yum --downloadonly ..' looks exiting with exit code 1 if
    # any downloads found.
    if rc == 0:
        LOG.info("No downloads.")
    elif rc == 1:
        LOG.info("Download: OK")
    else:
        LOG.error("Failed to download udpates: " + err)


DEFAULT_OUT_KEYS = dict(errata=["advisory", "type", "severity", "name",
                                "epoch", "version", "release", "arch", "url"],
                        default=_RPM_KEYS)


def load_conf(conf_path, sect="main"):
    cp = configparser.SafeConfigParser()
    try:
        cp.read(conf_path)
        d = dict(cp.items(sect))

        try:
            d["download"] = bool(int(d.get("download", '0')))
        except:
            d["download"] = False

        for k in ("disablerepos", "enablerepos"):
            d[k] = d.get(k).split(',')  # TODO: safer impl.

        return d
    except Exception:
        LOG.warn("Failed to load '%s'" % conf_path)
        raise

    return dict()


def ensure_not_none(val):
    if val is None:
        return ' '
    else:
        return val


def outputs_result(result, outdir, restype="updates", header_file=None,
                   keys=[]):
    """
    :param result: A list of result dicts :: [dict]
    :param outdir: Output dir
    :param restype: Result type
    :param header_file: Header text file embedded in CSV and JSON outputs
    :param keys: CSV headers
    """
    if not keys:
        keys = DEFAULT_OUT_KEYS.get(restype, DEFAULT_OUT_KEYS["default"])

    if result:
        keys = [k for k in keys if k in result[0]]

    result = sorted(result, key=operator.itemgetter(keys[0]))

    if not os.path.exists(outdir):
        os.makedirs(outdir)

    timestamp = _localtime()

    fpath = os.path.join(outdir, "timestamp.txt")
    f = open(fpath, 'w')
    f.write(timestamp + '\n')
    f.close()

    header = ""
    if header_file:
        header = _open(header_file).read()

    fpath = os.path.join(outdir, restype + ".json")
    f = open(fpath, 'w')
    LOG.info("Dump JSON data: " + fpath)
    json.dump(dict(data=result, header=header, timestamp=timestamp), f)
    f.close()

    fpath = os.path.join(outdir, restype + ".csv")
    f = _open(fpath, 'w')
    LOG.info("Dump CSV data: " + fpath)
    if not keys:
        keys = DEFAULT_OUT_KEYS.get(restype, DEFAULT_OUT_KEYS["default"])

    for h in header.splitlines():
        if h.startswith(u"#"):
            f.write(h + '\n')
        else:
            f.write(u"# " + h + '\n')

    f.write(','.join(keys) + '\n')

    for d in result:
        vals = [ensure_not_none(v) for v in (d.get(k, ' ') for k in keys)]
        f.write(','.join(v for v in vals) + '\n')
    f.close()


def _set_loglevel(lvl):
    if lvl not in (0, 1, 2):
        lvl = 0

    LOG.setLevel([logging.WARN, logging.INFO, logging.DEBUG][lvl])


_USAGE = """%prog [Options]

Examples:
  # Save installed, update rpms and errata list corresponding to 'yum list
  # installed' + 'yum check-updates' + 'yum list-sec':
  %prog --disablerepo='*' --enablerepo='rhel-x86_64-server-6' \\
     --root=/var/lib/yum_makelistcache/root.d/aaa

  # Similar to the above but also save update RPMs, similar to 'yum update
  # --downloadonly':
  %prog --disablerepo='*' --enablerepo='rhel-x86_64-server-6' \\
     --root=/var/lib/yum_makelistcache/root.d/aaa --download
"""

_DEFAULTS = dict(root=os.curdir, log=False,
                 enablerepos=[], disablerepos=[], download=False,
                 downloaddir=None, conf=None, outdir=None,
                 header_file=None, verbosity=0)


def option_parser(usage=_USAGE, defaults=_DEFAULTS):
    """
    :param usage: Usage text
    :param defaults: Option value defaults
    """
    p = optparse.OptionParser(usage)
    p.set_defaults(**defaults)

    p.add_option("-r", "--root", help="RPM DB root dir. By default, dir "
                 "in which the 'Packages' RPM DB exists or '../../../' "
                 "of that dir if 'Packages' exists under 'var/lib/rpm'.")
    p.add_option("", "--log", action="store_true",
                 help="Take run log ($logdir/%s.log) if given" % NAME)

    p.add_option('', "--enablerepo", action="append", dest="enablerepos",
                 help="specify additional repoids to query, can be "
                      "specified multiple times")
    p.add_option('', "--disablerepo", action="append", dest="disablerepos",
                 help="specify repoids to disable, can be specified "
                      "multiple times")

    p.add_option("-d", "--download", action="store_true",
                 help="Download update RPMs also")
    p.add_option("", "--downloaddir",
                 help="Dir to save update RPMs downloaded")

    p.add_option("-C", "--conf", help="Specify .ini style config file path")
    p.add_option("-O", "--outdir",
                 help="Specify outputs dir, ex. '/tmp/root/sys_a/' "
                      "[<root>/var/log/]")
    p.add_option("-v", "--verbose", action="count", dest="verbosity",
                 help="Verbose mode")
    p.add_option("-D", "--debug", action="store_const", dest="verbosity",
                 const=2, help="Debug mode (same as -vv)")

    p.add_option("-H", "--header-file",
                 help="Header text file (must be in UTF-8) embedded in CSV "
                      "and JSON outputs")
    return p


def main(argv=sys.argv, pkgnarrows=_PKG_NARROWS):
    p = option_parser()
    (options, args) = p.parse_args(argv[1:])

    if options.conf:
        diff = load_conf(options.conf)
        p.set_defaults(**diff)
        (options, args) = p.parse_args(argv[1:])

    _set_loglevel(options.verbosity)
    options.root = os.path.abspath(options.root)  # Ensure abspath.

    if not setup_root(options.root):
        LOG.error("setup_root failed. Aborting...")
        sys.exit(2)

    if options.log:
        logfile = logpath(options.root, NAME + ".log")
        LOG.info("Log will be saved to: " + logfile)
        LOG.addHandler(logging.FileHandler(logfile))

    if not options.outdir:
        options.outdir = os.path.join(options.root, "var/log")

    # Get errata list:
    es = yum_list_errata(options.root, options.enablerepos,
                         options.disablerepos)
    outputs_result(es, options.outdir, "errata", options.header_file)

    # Get installed and update rpms list:
    pkgs = yum_list(options.root, options.enablerepos, options.disablerepos)

    for narrow in pkgnarrows:
        pdicts = _pkgs2dicts(pkgs[narrow])
        outputs_result(pdicts, options.outdir, narrow, options.header_file)

    # ... and download update rpms if wanted:
    if options.download:
        yum_download(options.root, options.enablerepos, options.disablerepos,
                     options.downloaddir)


if __name__ == '__main__':
    main()

# vim:sw=4:ts=4:et:
