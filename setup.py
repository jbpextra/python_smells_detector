from setuptools import setup, find_packages

setup(
    name="code_quality_analyzer",
    version="0.1",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "astroid>=3.0",
        "networkx>=3.1",
        "pyyaml>=6.0",
        "pathspec>=0.12",
    ],
    python_requires=">=3.7",
    include_package_data=True,
    package_data={"code_quality_analyzer": ["code_quality_config.yaml"]},
    entry_points={
        "console_scripts": [
            "analyze_code_quality=code_quality_analyzer.main:analyze_project",
        ],
    },
    author="Karthik Shivashankar",
    author_email="karthik13sankar@outlook.com",
    description="A tool to detect code smells, architectural smells, and structural smells in Python projects",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/KarthikShivasankar/code_quality_analyzer",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    extras_require={
        'test': [
            'pytest>=7.0',
        ],
        'docs': [
            'sphinx>=4.0',
            'sphinx-rtd-theme>=1.0',
            'sphinx-autodoc-typehints>=1.12',
            'myst-parser>=0.15',
        ],
        'dev': [
            'pytest>=7.0',
            'black>=24.0',
            'sphinx>=4.0',
            'sphinx-rtd-theme>=1.0',
            'sphinx-autodoc-typehints>=1.12',
            'myst-parser>=0.15',
        ],
    },
)
