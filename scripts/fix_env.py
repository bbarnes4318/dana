with open('/workspace/dana/.env', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if any(k in line for k in ('DANA_FORCE_DIAGNOSTIC_GREETING', 'DANA_DIAGNOSTIC_GREETING_TEXT', '# Diagnostic greeting')):
        continue
    new_lines.append(line)

new_lines.append('\n# Diagnostic greeting\n')
new_lines.append('DANA_FORCE_DIAGNOSTIC_GREETING=true\n')
new_lines.append('DANA_DIAGNOSTIC_GREETING_TEXT="Hello, can you hear me?"\n')

with open('/workspace/dana/.env', 'w') as f:
    f.writelines(new_lines)

print("FIXED ENV")
