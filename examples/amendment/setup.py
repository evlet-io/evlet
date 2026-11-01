from setuptools import find_packages, setup

setup(packages=['amendment', *['amendment.' + name for name in find_packages('.')]])
