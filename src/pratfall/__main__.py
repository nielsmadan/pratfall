import signal
import sys

if sys.argv[1:2] == ["__step"] and signal.getsignal(signal.SIGINT) is signal.default_int_handler:
    signal.signal(signal.SIGINT, signal.SIG_DFL)

from pratfall.cli import main

sys.exit(main())
