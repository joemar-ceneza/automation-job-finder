"""
Tests for per-site politeness delays and the default site list.

A single shared delay stops being right once the sites differ this much in
tolerance: 3 seconds is unremarkable to JobStreet and rude to LinkedIn. And a
default site list that includes the two sites most likely to ban you means
every scheduled run spends that risk without anyone choosing to.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


# ======================================================
# PER-SITE DELAYS
# ======================================================
def test_the_aggressive_sites_wait_longer():
    assert config.delay_for("linkedin") > config.delay_for("jobstreet")
    assert config.delay_for("indeed") > config.delay_for("jobstreet")


def test_every_known_site_has_a_delay():
    for site in config.SELECTORS:
        assert config.delay_for(site) > 0


def test_an_unknown_site_falls_back_to_the_global_default():
    assert config.delay_for("nonesuch") == config.DEFAULT_DELAY_SECONDS


def test_no_delay_is_below_the_politeness_floor():
    """The 3-second floor is load-bearing, not decoration."""
    for site in config.SELECTORS:
        assert config.delay_for(site) >= 3.0


# ======================================================
# DEFAULT SITE LIST
# ======================================================
def test_the_default_run_skips_the_sites_that_block():
    """
    LinkedIn and Indeed stay opt-in. Both restrict automated collection and
    Indeed actively challenges it, so a bare run should not hit them.
    """
    assert "linkedin" not in config.DEFAULT_SITES
    assert "indeed" not in config.DEFAULT_SITES


def test_the_default_sites_are_the_reliable_ones():
    assert set(config.DEFAULT_SITES) == {"jobstreet", "onlinejobs", "kalibrr"}


def test_every_default_site_has_selectors():
    for site in config.DEFAULT_SITES:
        assert site in config.SELECTORS


def test_the_opt_in_sites_are_still_fully_configured():
    """Excluded from the default list, not removed — --site must still work."""
    for site in ("linkedin", "indeed"):
        assert site in config.SELECTORS
        assert config.SELECTORS[site]["job_card"]
