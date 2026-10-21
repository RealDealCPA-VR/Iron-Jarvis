"""Settings for each place the service runs."""


class Development:
    NAME = "development"
    HOST = "localhost"
    TIMEOUT = 30
    DEBUG = True


class Production:
    NAME = "production"
    HOST = "service.example.com"
    TIMEOUT = 60
    DEBUG = False


def settings_for(name):
    for env in (Development, Production):
        if env.NAME == name:
            return env
    raise KeyError(name)
