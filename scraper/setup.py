from setuptools import setup, find_packages

setup(
    name="gmaps_scraper_server",
    version="0.1",
    packages=find_packages(),
    install_requires=[
        "playwright",
        "fastapi",
        "uvicorn[standard]"
    ],
)