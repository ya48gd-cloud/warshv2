content = open('app/main.py', encoding='utf-8').read()
lines = content.split('\n')
for i in range(55, 70):
    print(f'Line {i+1}: {repr(lines[i])}')
