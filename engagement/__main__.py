import sys

if len(sys.argv) > 1 and sys.argv[1] == "walkthrough":
    from .walkthrough import main
    sys.exit(main(sys.argv[2:]))

from .demo import main

sys.exit(main())
