#!/usr/bin/env python3
from setuptools import find_packages, setup

__version__ = "0.5"


def load_requirements(filename):
    with open(filename, "rt") as fh:
        return fh.read().rstrip().split("\n")


def long_description():
    with open("README.md") as f:
        return f.read()


setup(
    name="ptyrc",
    version=__version__,
    author="plcp",
    author_email="plcp.me@pm.me",
    license="GPL-3.0-only",
    packages=find_packages(),
    url="https://gitlab.com/plcp/ptyrc",
    description="the pty remote controller",
    long_description=long_description(),
    long_description_content_type="text/markdown",
    install_requires=load_requirements("requirements.txt"),
    entry_points={
            'console_scripts': [
                'ptyrc-driver = ptyrc.driver:main',
                'ptyrc-pilot = ptyrc.pilot:main',
            ]
    },
)
