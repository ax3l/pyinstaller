#-----------------------------------------------------------------------------
# Copyright (c) 2013, PyInstaller Development Team.
#
# Distributed under the terms of the GNU General Public License with exception
# for distributing bootloader.
#
# The full license is in the file COPYING.txt, distributed with this software.
#-----------------------------------------------------------------------------


import os
import sys

import PyInstaller.depend.bindepend

from PyInstaller.compat import is_win, is_darwin, is_unix, is_venv, base_prefix
from PyInstaller.compat import modname_tkinter
from PyInstaller.build import Tree
from PyInstaller.utils.hooks.hookutils import exec_statement, logger


def _handle_broken_tk():
    """
    Workaround for broken Tcl/Tk detection in virtualenv on Windows.

    There is a bug in older versions of virtualenv in setting paths
    to Tcl/Tk properly. PyInstaller running in virtualenv is then
    not able to find Tcl/Tk.

    This issue has been experienced in virtualenv with Python 2.4 on Win7.

    https://github.com/pypa/virtualenv/issues/93
    """
    if is_win and is_venv:
        basedir = os.path.join(base_prefix, 'tcl')
        files = os.listdir(basedir)
        v = os.environ
        # Detect Tcl/Tk paths.
        for f in files:
            abs_path = os.path.join(basedir, f)
            if f.startswith('tcl') and os.path.isdir(abs_path):
                v['TCL_LIBRARY'] = abs_path
            if f.startswith('tk') and os.path.isdir(abs_path):
                v['TK_LIBRARY'] = abs_path
            if f.startswith('tix') and os.path.isdir(abs_path):
                v['TIX_LIBRARY'] = abs_path


def _warn_if_actvivetcl_or_teapot_install(tcl_root, tcltree):
    """
    Workaround ActiveTcl on OS X

    PyInstaller does not package all requirements of ActiveTcl
    (most notably teapot, which is not typically required). This
    means packages built against ActiveTcl usually won't run on
    non-host systems.

    This method checks if ActiveTcl is being used, and if so logs
    a warning if the problematic code is not commented out.

    https://github.com/pyinstaller/pyinstaller/issues/621
    """

    from PyInstaller.lib.macholib import util
    if util.in_system_path(tcl_root):
        # system libraries do not experience this problem
        return

    # get the path to the 'init.tcl' script
    try:
        init_resource = [r[1] for r in tcltree if r[1].endswith('init.tcl')][0]
    except IndexError:
        # couldn't find the init script, return
        return

    mentions_activetcl = False
    mentions_teapot = False
    with open(init_resource, 'r') as init_file:
        for line in init_file.readlines():
            line = line.strip().lower()
            if line.startswith('#'):
                continue
            if 'activetcl' in line:
                mentions_activetcl = True
            if 'teapot' in line:
                mentions_teapot = True
            if mentions_activetcl and mentions_teapot:
                break

    if mentions_activetcl and mentions_teapot:
        logger.warning("""It seems you are using an ActiveTcl build of Tcl/Tk.\
 This may not package correctly with PyInstaller.
To fix the problem, please try commenting out all mentions of 'teapot' in:

     %s

See https://github.com/pyinstaller/pyinstaller/issues/621 for more information"""
                       % init_resource)


def _find_tk_darwin_frameworks(binaries):
    """
    Tcl and Tk are installed as Mac OS X Frameworks.
    """
    tcl_root = tk_root = None
    for nm, fnm in binaries:
        if nm == 'Tcl':
            tcl_root = os.path.join(os.path.dirname(fnm), 'Resources/Scripts')
        if nm == 'Tk':
            tk_root = os.path.join(os.path.dirname(fnm), 'Resources/Scripts')
    return tcl_root, tk_root


def _find_tk_tclshell():
    """
    Get paths to Tcl/Tk from the Tcl shell command 'info library'.

    This command will return path to TCL_LIBRARY.
    On most systems are Tcl and Tk libraries installed
    in the same prefix.
    """
    tcl_root = tk_root = None

    # Python code to get path to TCL_LIBRARY.
    code = 'from %s import Tcl; t = Tcl(); print(t.eval("info library"))' % modname_tkinter

    tcl_root = exec_statement(code)
    tk_version = exec_statement('from _tkinter import TK_VERSION as v; print(v)')
    # TK_LIBRARY is in the same prefix as Tcl.
    tk_root = os.path.join(os.path.dirname(tcl_root), 'tk%s' % tk_version)
    return tcl_root, tk_root


def _find_tk(mod):
    """
    Find paths with Tcl and Tk data files to be bundled by PyInstaller.

    Return:
        tcl_root  path to Tcl data files.
        tk_root   path to Tk data files.
    """
    bins = PyInstaller.depend.bindepend.selectImports(mod.__file__)

    if is_darwin:
        # _tkinter depends on system Tcl/Tk frameworks.
        # For example this is the case of Python from homebrew.
        if not bins:
            # 'mod.binaries' can't be used because on Mac OS X _tkinter.so
            # might depend on system Tcl/Tk frameworks and these are not
            # included in 'mod.binaries'.
            bins = PyInstaller.depend.bindepend.getImports(mod.__file__)
            # Reformat data structure from
            #     set(['lib1', 'lib2', 'lib3'])
            # to
            #     [('Tcl', '/path/to/Tcl'), ('Tk', '/path/to/Tk')]
            mapping = {}
            for l in bins:
                mapping[os.path.basename(l)] = l
            bins = [
                ('Tcl', mapping['Tcl']),
                ('Tk', mapping['Tk']),
            ]

        # _tkinter depends on Tcl/Tk compiled as frameworks.
        path_to_tcl = bins[0][1]
        if 'Library/Frameworks' in path_to_tcl:
            tcl_tk = _find_tk_darwin_frameworks(bins)
        # Tcl/Tk compiled as on Linux other Unixes.
        # For example this is the case of Tcl/Tk from macports.
        else:
            tcl_tk = _find_tk_tclshell()

    else:
        tcl_tk = _find_tk_tclshell()

    return tcl_tk


def _collect_tkfiles(mod):
    # Workaround for broken Tcl/Tk detection in virtualenv on Windows.
    _handle_broken_tk()

    tcl_root, tk_root = _find_tk(mod)

    if not tcl_root:
        logger.error("TCL/TK seams to be not properly installed on this system")
        return []

    tcldir = "tcl"
    tkdir = "tk"

    tcltree = Tree(tcl_root, os.path.join('_MEI', tcldir),
                   excludes=['demos', '*.lib', 'tclConfig.sh'])

    if is_darwin:
        # handle workaround for ActiveTcl on OS X
        _warn_if_actvivetcl_or_teapot_install(tcl_root, tcltree)

    tktree = Tree(tk_root, os.path.join('_MEI', tkdir),
                  excludes=['demos', '*.lib', 'tkConfig.sh'])
    return (tcltree + tktree)


def hook(mod):
    # If not supported platform, skip TCL/TK detection.
    if not (is_win or is_darwin or is_unix):
        logger.info("... skipping TCL/TK detection on this platform (%s)",
                    sys.platform)
        return mod

    # Get the Tcl/Tk data files for bundling with executable.
    #try:
    tk_files = _collect_tkfiles(mod)
    mod.datas.extend(tk_files)
    #except:
    #logger.error("could not find TCL/TK")

    return mod
