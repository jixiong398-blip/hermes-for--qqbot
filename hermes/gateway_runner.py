import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
os.environ['PYTHONUTF8'] = '1'
from hermes_cli.main import main
main()
