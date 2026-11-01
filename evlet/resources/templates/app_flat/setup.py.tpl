from setuptools import find_packages, setup

setup(packages=['{{name}}', *['{{name}}.' + name for name in find_packages('.')
                            if name.split('.')[0] in {'models', 'reports', 'services', 'migrations'}]])
