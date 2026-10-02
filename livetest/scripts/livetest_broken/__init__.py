"""A player script that fails on purpose. Live tests check it is logged with its traceback and harms nothing."""


def setup(game):
    raise RuntimeError("livetest_broken setup raises on purpose")
