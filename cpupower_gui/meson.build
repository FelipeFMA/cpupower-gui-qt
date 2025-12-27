python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', join_paths(prefix, get_option('localedir')))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'cpupower-gui.in',
  output: 'cpupower-gui',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir')
)

configure_file(
  input: 'cpupower-gui-helper.py.in',
  output: 'cpupower-gui-helper',
  configuration: conf,
  install: true,
  install_dir: helperdir
)

cpupower_gui_sources = [
  '__init__.py',
  'main.py',
  'window.py',
  'config.py',
  'utils.py',
  'helper.py'
]

install_data(cpupower_gui_sources, install_dir: moduledir)
