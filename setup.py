from setuptools import setup

LONG_DESC = open("README.rst").read()

setup(
    name="distkv_inv",
    use_scm_version={"version_scheme": "guess-next-dev", "local_scheme": "dirty-tag"},
    description="Inventory management for DistKV",
    url="https://github.com/smurfix/distinv",
    long_description=LONG_DESC,
    author="Matthias Urlichs",
    author_email="matthias@urlichs.de",
    license="MIT -or- Apache License 2.0",
    packages=["distkv_ext.inv"],
    setup_requires=["setuptools_scm", "pytest-runner", "trustme >= 0.5"],
    install_requires=["distkv >= 0.55","netifaces"],
    tests_require=["trustme >= 0.5", "pytest", "flake8 >= 3.7"],
    keywords=["async", "key-values", "distributed"],
    python_requires=">=3.7",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Information Technology",
        "License :: OSI Approved :: MIT License",
        "License :: OSI Approved :: Apache Software License",
        "Framework :: AsyncIO",
        "Framework :: Trio",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Database",
        "Topic :: Home Automation",
        "Topic :: System :: Distributed Computing",
    ],
    zip_safe=True,
)
