# Licensed under a 3-clause BSD style license - see LICENSE.rst
"""
These plugins modify the behavior of py.test and are meant to be imported
into conftest.py in the root directory.
"""

import ast
import datetime
import locale
import os
import sys
import types
from collections import OrderedDict

import pytest

from ..config.paths import set_temp_config, set_temp_cache
from .helper import treat_deprecations_as_exceptions, ignore_warnings
from .helper import enable_deprecations_as_exceptions  # pylint: disable=W0611
from ..utils.argparse import writeable_directory
from ..utils.introspection import resolve_name

import importlib.machinery as importlib_machinery

pytest_plugins = ('astropy.tests.pytest_doctestplus',
                  'astropy.tests.pytest_openfiles',
                  'astropy.tests.pytest_repeat',
                  'astropy.tests.pytest_remotedata')

# these pytest hooks allow us to mark tests and run the marked tests with
# specific command line options.


def pytest_addoption(parser):

    parser.addoption("--config-dir", nargs='?', type=writeable_directory,
                     help="specify directory for storing and retrieving the "
                          "Astropy configuration during tests (default is "
                          "to use a temporary directory created by the test "
                          "runner); be aware that using an Astropy config "
                          "file other than the default can cause some tests "
                          "to fail unexpectedly")

    parser.addoption("--cache-dir", nargs='?', type=writeable_directory,
                     help="specify directory for storing and retrieving the "
                          "Astropy cache during tests (default is "
                          "to use a temporary directory created by the test "
                          "runner)")
    parser.addini("config_dir",
                  "specify directory for storing and retrieving the "
                  "Astropy configuration during tests (default is "
                  "to use a temporary directory created by the test "
                  "runner); be aware that using an Astropy config "
                  "file other than the default can cause some tests "
                  "to fail unexpectedly", default=None)

    parser.addini("cache_dir",
                  "specify directory for storing and retrieving the "
                  "Astropy cache during tests (default is "
                  "to use a temporary directory created by the test "
                  "runner)", default=None)


def pytest_configure(config):
    treat_deprecations_as_exceptions()

def pytest_runtest_setup(item):
    config_dir = item.config.getini('config_dir')
    cache_dir = item.config.getini('cache_dir')

    # Command-line options can override, however
    config_dir = item.config.getoption('config_dir') or config_dir
    cache_dir = item.config.getoption('cache_dir') or cache_dir

    # We can't really use context managers directly in py.test (although
    # py.test 2.7 adds the capability), so this may look a bit hacky
    if config_dir:
        item.set_temp_config = set_temp_config(config_dir)
        item.set_temp_config.__enter__()
    if cache_dir:
        item.set_temp_cache = set_temp_cache(cache_dir)
        item.set_temp_cache.__enter__()



def pytest_runtest_teardown(item, nextitem):
    if hasattr(item, 'set_temp_cache'):
        item.set_temp_cache.__exit__()
    if hasattr(item, 'set_temp_config'):
        item.set_temp_config.__exit__()


PYTEST_HEADER_MODULES = OrderedDict([('Numpy', 'numpy'),
                                     ('Scipy', 'scipy'),
                                     ('Matplotlib', 'matplotlib'),
                                     ('h5py', 'h5py'),
                                     ('Pandas', 'pandas')])

# This always returns with Astropy's version
from .. import __version__

TESTED_VERSIONS = OrderedDict([('Astropy', __version__)])


def pytest_report_header(config):

    try:
        stdoutencoding = sys.stdout.encoding or 'ascii'
    except AttributeError:
        stdoutencoding = 'ascii'

    args = config.args

    # TESTED_VERSIONS can contain the affiliated package version, too
    if len(TESTED_VERSIONS) > 1:
        for pkg, version in TESTED_VERSIONS.items():
            if pkg != 'Astropy':
                s = "\nRunning tests with {0} version {1}.\n".format(
                    pkg, version)
    else:
        s = "\nRunning tests with Astropy version {0}.\n".format(
            TESTED_VERSIONS['Astropy'])

    # Per https://github.com/astropy/astropy/pull/4204, strip the rootdir from
    # each directory argument
    if hasattr(config, 'rootdir'):
        rootdir = str(config.rootdir)
        if not rootdir.endswith(os.sep):
            rootdir += os.sep

        dirs = [arg[len(rootdir):] if arg.startswith(rootdir) else arg
                for arg in args]
    else:
        dirs = args

    s += "Running tests in {0}.\n\n".format(" ".join(dirs))

    s += "Date: {0}\n\n".format(datetime.datetime.now().isoformat()[:19])

    from platform import platform
    plat = platform()
    if isinstance(plat, bytes):
        plat = plat.decode(stdoutencoding, 'replace')
    s += "Platform: {0}\n\n".format(plat)
    s += "Executable: {0}\n\n".format(sys.executable)
    s += "Full Python Version: \n{0}\n\n".format(sys.version)

    s += "encodings: sys: {0}, locale: {1}, filesystem: {2}".format(
        sys.getdefaultencoding(),
        locale.getpreferredencoding(),
        sys.getfilesystemencoding())
    s += '\n'

    s += "byteorder: {0}\n".format(sys.byteorder)
    s += "float info: dig: {0.dig}, mant_dig: {0.dig}\n\n".format(
        sys.float_info)

    for module_display, module_name in PYTEST_HEADER_MODULES.items():
        try:
            with ignore_warnings(DeprecationWarning):
                module = resolve_name(module_name)
        except ImportError:
            s += "{0}: not available\n".format(module_display)
        else:
            try:
                version = module.__version__
            except AttributeError:
                version = 'unknown (no __version__ attribute)'
            s += "{0}: {1}\n".format(module_display, version)

    special_opts = ["remote_data", "pep8"]
    opts = []
    for op in special_opts:
        op_value = getattr(config.option, op, None)
        if op_value:
            if isinstance(op_value, str):
                op = ': '.join((op, op_value))
            opts.append(op)
    if opts:
        s += "Using Astropy options: {0}.\n".format(", ".join(opts))

    return s


def pytest_pycollect_makemodule(path, parent):
    # This is where we set up testing both with and without

    return pytest.Module(path, parent)


class Pair(pytest.File):
    """
    This class treats a given test .py file as a pair of .py files
    where one has __future__ unicode_literals and the other does not.
    """

    def collect(self):
        # First, just do the regular import of the module to make
        # sure it's sane and valid.  This block is copied directly
        # from py.test
        try:
            mod = self.fspath.pyimport(ensuresyspath=True)
        except SyntaxError:
            import py
            excinfo = py.code.ExceptionInfo()
            raise self.CollectError(excinfo.getrepr(style="short"))
        except self.fspath.ImportMismatchError:
            e = sys.exc_info()[1]
            raise self.CollectError(
                "import file mismatch:\n"
                "imported module {!r} has this __file__ attribute:\n"
                "  {}\n"
                "which is not the same as the test file we want to collect:\n"
                "  {}\n"
                "HINT: remove __pycache__ / .pyc files and/or use a "
                "unique basename for your test file modules".format(e.args))

        return [pytest.Module(self.fspath, self)]


class ModifiedModule(pytest.Module):
    def __init__(self, mod_name, content, path, parent):
        self.mod_name = mod_name
        self.content = content
        super(ModifiedModule, self).__init__(path, parent)

    def _importtestmodule(self):
        content = self.content

        new_mod = types.ModuleType(self.mod_name)
        new_mod.__file__ = str(self.fspath)

        if hasattr(self, '_transform_ast'):
            # ast.parse doesn't let us hand-select the __future__
            # statements, but built-in compile, with the PyCF_ONLY_AST
            # flag does.
            tree = compile(
                content, str(self.fspath), 'exec',
                self.flags | ast.PyCF_ONLY_AST, True)
            tree = self._transform_ast(tree)
            # Now that we've transformed the tree, recompile it
            code = compile(
                tree, str(self.fspath), 'exec')
        else:
            # If we don't need to transform the AST, we can skip
            # parsing/compiling in two steps
            code = compile(
                content, str(self.fspath), 'exec',
                self.flags, True)

        pwd = os.getcwd()
        try:
            os.chdir(os.path.dirname(str(self.fspath)))
            exec(code, new_mod.__dict__)
        finally:
            os.chdir(pwd)
        self.config.pluginmanager.consider_module(new_mod)
        return new_mod


def pytest_terminal_summary(terminalreporter):
    """Output a warning to IPython users in case any tests failed."""

    try:
        get_ipython()
    except NameError:
        return

    if not terminalreporter.stats.get('failed'):
        # Only issue the warning when there are actually failures
        return

    terminalreporter.ensure_newline()
    terminalreporter.write_line(
        'Some tests are known to fail when run from the IPython prompt; '
        'especially, but not limited to tests involving logging and warning '
        'handling.  Unless you are certain as to the cause of the failure, '
        'please check that the failure occurs outside IPython as well.  See '
        'http://docs.astropy.org/en/stable/known_issues.html#failing-logging-'
        'tests-when-running-the-tests-in-ipython for more information.',
        yellow=True, bold=True)
