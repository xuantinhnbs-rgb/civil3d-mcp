import sys
from pathlib import Path

# Cho phép chạy pytest từ bất kỳ thư mục nào mà vẫn import được gói civil3d_mcp.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
