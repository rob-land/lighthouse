desktop_file = i18n.merge_file(
  input:  'land.rob.lighthouse.desktop.in',
  output: 'land.rob.lighthouse.desktop',
  type:   'desktop',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'applications',
)

desktop_utils = find_program('desktop-file-validate', required: false)
if desktop_utils.found()
  test('Validate desktop file', desktop_utils, args: [desktop_file])
endif

appstream_file = i18n.merge_file(
  input:  'land.rob.lighthouse.metainfo.xml.in',
  output: 'land.rob.lighthouse.metainfo.xml',
  po_dir: '../po',
  install: true,
  install_dir: datadir / 'metainfo',
)

appstream_util = find_program('appstreamcli', required: false)
if appstream_util.found()
  test(
    'Validate metainfo',
    appstream_util,
    args: ['validate', '--no-net', appstream_file],
  )
endif

install_data(
  'land.rob.lighthouse.gschema.xml',
  install_dir: datadir / 'glib-2.0' / 'schemas',
)

# D-Bus session activation for the agent (lets the GUI start it on demand).
configure_file(
  input:         'land.rob.lighthouse.Agent.service.in',
  output:        'land.rob.lighthouse.Agent.service',
  configuration: conf,
  install:       true,
  install_dir:   datadir / 'dbus-1' / 'services',
)

# systemd-user unit for the always-on agent (needs `loginctl enable-linger`).
configure_file(
  input:         'land.rob.lighthouse.service.in',
  output:        'land.rob.lighthouse.service',
  configuration: conf,
  install:       true,
  install_dir:   prefix / 'lib' / 'systemd' / 'user',
)

install_data(
  'icons/hicolor/scalable/apps/land.rob.lighthouse.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'scalable' / 'apps',
)
install_data(
  'icons/hicolor/symbolic/apps/land.rob.lighthouse-symbolic.svg',
  install_dir: datadir / 'icons' / 'hicolor' / 'symbolic' / 'apps',
)

compile_schemas = find_program('glib-compile-schemas', required: false)
if compile_schemas.found()
  test(
    'Validate schema',
    compile_schemas,
    args: ['--strict', '--dry-run', meson.current_source_dir()],
  )
endif

# UI templates -> Blueprint -> .ui -> GResource bundle
subdir('ui')
