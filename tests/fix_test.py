with open('tests/test_model.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the broken import lines
old = 'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'
new = 'sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))'

if old in content:
    content = content.replace(old, new)
    print("Fixed import path")
else:
    print("Import path already correct or different format")

# Also fix __main__ line at bottom
old_main = "pytest.main([__file__, '-v', '--tb=short'])"
new_main = "pytest.main([__file__, '-v', '--tb=short'])"

with open('tests/test_model.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done!")
