# Fix Unicode status icons in agent_coordinator.py
import os

# agent_coordinator.py is in the same directory as this script's parent
path = r'D:\python_files_code\mh-deepseek-main-updated\agent_coordinator.py'

with open(path, 'rb') as f:
    data = f.read()

print('Before:', len(data), 'bytes')

# Unicode -> ASCII replacements  
data = data.replace(b'\xe2\x96\xb6', b'>')   # ▶
data = data.replace(b'\xe2\x8f\xb8', b'||')  # ⏸
data = data.replace(b'\xe2\x9c\x93', b'+')   # ✓
data = data.replace(b'\xe2\x96\xa0', b'#')   # ■
data = data.replace(b'\xe2\x9c\x97', b'x')   # ✗
data = data.replace(b'\xe2\x97\x8b', b'o')   # ○

print('After:', len(data), 'bytes')

with open(path, 'wb') as f:
    f.write(data)

# Verify
with open(path, 'rb') as f:
    verify = f.read()
print('Verified:', len(verify), 'bytes, unicode_left:', b'\xe2\x96\xb6' in verify)
print('DONE')
