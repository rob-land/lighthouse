# Runs against the source tree (PYTHONPATH=src); the package __init__
# falls back to dev constants when const.py hasn't been generated.
test(
  'link-pairing',
  python,
  args: [files('test_link.py')],
  env: ['PYTHONPATH=' + meson.project_source_root() / 'src'],
  timeout: 90,
)
