# MIT License
#
# Copyright (c) 2018-2019 Red Hat, Inc.

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
from typing import List, Tuple, TYPE_CHECKING

import pytest
from copr.v3 import Client
from fedora.client import AuthError, FedoraServiceError
from fedora.client.fas2 import AccountSystem
from flexmock import flexmock

from ogr.abstract import GitProject, GitService, CommitStatus
from ogr.services.github import GithubProject, GithubService
from packit.config import JobType, JobConfig, JobConfigTriggerType
from packit.config.job_config import JobMetadataConfig
from packit.copr_helper import CoprHelper
from packit.local_project import LocalProject
from packit_service.config import Deployment
from packit_service.constants import FAQ_URL
from packit_service.models import WhitelistModel as DBWhitelist
from packit_service.service.events import (
    ReleaseEvent,
    PullRequestEvent,
    PullRequestCommentEvent,
    PullRequestAction,
    PullRequestCommentAction,
    IssueCommentEvent,
    IssueCommentAction,
    AbstractGithubEvent,
)
from packit_service.service.events import WhitelistStatus
from packit_service.worker.reporting import StatusReporter
from packit_service.worker.whitelist import Whitelist

EXPECTED_TESTING_FARM_CHECK_NAME = f"packit-stg/testing-farm-fedora-rawhide-x86_64"


@pytest.fixture()
def whitelist():
    w = Whitelist()
    return w


@pytest.mark.parametrize(
    "account_name, model, is_approved",
    (
        (
            "fero",
            flexmock(
                id=1,
                account_name="fero",
                status=WhitelistStatus.approved_manually.value,
            ),
            True,
        ),
        (
            "lojzo",
            flexmock(
                id=2,
                account_name="lojzo",
                status=WhitelistStatus.approved_automatically.value,
            ),
            True,
        ),
        (
            "konipas",
            flexmock(
                id=3, account_name="konipas", status=WhitelistStatus.waiting.value
            ),
            False,
        ),
        ("krasomila", None, False),
    ),
)
def test_is_approved(whitelist, account_name, model, is_approved):
    flexmock(DBWhitelist).should_receive("get_account").and_return(model)
    assert whitelist.is_approved(account_name) == is_approved


@pytest.mark.parametrize(
    "account_name,person_object,raises,signed_fpca",
    [
        (
            "me",
            {
                "memberships": [
                    {"name": "unicorns"},
                    {"name": "cla_fpca"},
                    {"name": "builder"},
                ]
            },
            None,
            True,
        ),
        ("you", {"memberships": [{"name": "packager"}]}, None, False),
        ("they", {}, None, False),
        ("parrot", {"some": "data"}, None, False),
        ("we", None, AuthError, False),
        ("bear", None, FedoraServiceError, False),
    ],
)
def test_signed_fpca(whitelist, account_name, person_object, raises, signed_fpca):
    fas = (
        flexmock(AccountSystem)
        .should_receive("person_by_username")
        .with_args(account_name)
        .once()
    )
    if person_object is not None:
        fas.and_return(person_object)
    if raises is not None:
        fas.and_raise(raises)

    assert whitelist._signed_fpca(account_name) is signed_fpca


@pytest.mark.parametrize(
    "event, method, approved",
    [
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created, 0, "foo", "", "", "", "", "bar", "",
            ),
            "pr_comment",
            False,
        ),
        (
            IssueCommentEvent(
                IssueCommentAction.created, 0, "foo", "", "", "", "bar", "",
            ),
            "issue_comment",
            False,
        ),
        (
            PullRequestCommentEvent(
                PullRequestCommentAction.created, 0, "", "", "", "", "", "lojzo", "",
            ),
            "pr_comment",
            True,
        ),
        (
            IssueCommentEvent(
                IssueCommentAction.created, 0, "", "", "", "", "lojzo", "",
            ),
            "issue_comment",
            True,
        ),
    ],
)
def test_check_and_report_calls_method(whitelist, event, method, approved):
    gp = GitProject("", GitService(), "")
    mocked_gp = (
        flexmock(gp)
        .should_receive(method)
        .with_args(0, "Neither account bar nor owner foo are on our whitelist!")
    )
    mocked_gp.never() if approved else mocked_gp.once()
    whitelist_mock = flexmock(DBWhitelist).should_receive("get_account")
    if approved:
        whitelist_mock.and_return(DBWhitelist(status="approved_manually"))
    else:
        whitelist_mock.and_return(None)
    assert (
        whitelist.check_and_report(
            event, gp, config=flexmock(deployment=Deployment.stg)
        )
        is approved
    )


@pytest.fixture()
def events(request) -> List[Tuple[AbstractGithubEvent, bool]]:
    """
    :param request: event type to create Event instances of that type
    :return: list of Events that check_and_report accepts together with whether they should pass
    """
    approved_accounts = [
        ("foo", "bar", False),
        ("foo", "lojzo", True),
        ("lojzo", "bar", True),
        ("lojzo", "fero", True),
    ]

    if request.param == "release":
        return [
            (ReleaseEvent("foo", "", "", ""), False),
            (ReleaseEvent("lojzo", "", "", ""), True),
        ]
    elif request.param == "pr":
        return [
            (
                PullRequestEvent(
                    PullRequestAction.opened, 1, namespace, "", "", "", "", "", login
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    elif request.param == "pr_comment":
        return [
            (
                PullRequestCommentEvent(
                    PullRequestCommentAction.created,
                    1,
                    namespace,
                    "",
                    "",
                    "",
                    "",
                    login,
                    "",
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    elif request.param == "issue_comment":
        return [
            (
                IssueCommentEvent(
                    IssueCommentAction.created, 1, namespace, "", "", "", login, "",
                ),
                approved,
            )
            for namespace, login, approved in approved_accounts
        ]
    return []


# https://stackoverflow.com/questions/35413134/what-does-indirect-true-false-in-pytest-mark-parametrize-do-mean
@pytest.mark.parametrize(
    "events", ["release", "pr", "pr_comment", "issue_comment"], indirect=True,
)
def test_check_and_report(
    whitelist: Whitelist, events: List[Tuple[AbstractGithubEvent, bool]]
):
    """
    :param whitelist: fixture
    :param events: fixture: [(Event, should-be-approved)]
    """
    flexmock(
        GithubProject,
        pr_comment=lambda *args, **kwargs: None,
        set_commit_status=lambda *args, **kwargs: None,
        issue_comment=lambda *args, **kwargs: None,
    )
    flexmock(PullRequestEvent).should_receive("get_package_config").and_return(
        flexmock(
            jobs=[
                JobConfig(
                    type=JobType.tests,
                    trigger=JobConfigTriggerType.pull_request,
                    metadata=JobMetadataConfig(targets=["fedora-rawhide"]),
                )
            ],
        )
    )

    git_project = GithubProject("", GithubService(), "")
    for event, is_valid in events:
        if isinstance(event, PullRequestEvent) and not is_valid:
            # Report the status
            flexmock(CoprHelper).should_receive("get_copr_client").and_return(
                Client(
                    config={
                        "copr_url": "https://copr.fedorainfracloud.org",
                        "username": "some-owner",
                    }
                )
            )
            flexmock(LocalProject).should_receive("refresh_the_arguments").and_return(
                None
            )
            flexmock(LocalProject).should_receive("checkout_pr").and_return(None)
            flexmock(StatusReporter).should_receive("report").with_args(
                description="Account is not whitelisted!",
                state=CommitStatus.error,
                url=FAQ_URL,
                check_names=[EXPECTED_TESTING_FARM_CHECK_NAME],
            ).once()

        # get_account returns the whitelist object if it exists
        # returns nothing if it isn't whitelisted
        # then inside the whitelist.py file, a function checks if the status is
        # one of the approved statuses

        # this exact code is used twice above but mypy has an issue with this one only
        whitelist_mock = flexmock(DBWhitelist).should_receive("get_account")
        if not TYPE_CHECKING:
            if is_valid:
                whitelist_mock.and_return(DBWhitelist(status="approved_manually"))
            else:
                whitelist_mock.and_return(None)

        assert (
            whitelist.check_and_report(
                event,
                git_project,
                config=flexmock(deployment=Deployment.stg, command_handler_work_dir=""),
            )
            is is_valid
        )
