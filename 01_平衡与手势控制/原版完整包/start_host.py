from pathlib import Path
import sys
r=Path(__file__).resolve().parent
sys.path[:0]=[str(r),str(r/"vendor")]
from host.app import main
main()
