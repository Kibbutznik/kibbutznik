import enum


class CommunityStatus(int, enum.Enum):
    ACTIVE = 1
    INACTIVE = 2


class MemberStatus(int, enum.Enum):
    ACTIVE = 1
    THROWN_OUT = 2


class ProposalType(str, enum.Enum):
    MEMBERSHIP = "Membership"
    THROW_OUT = "ThrowOut"
    ADD_STATEMENT = "AddStatement"
    REMOVE_STATEMENT = "RemoveStatement"
    REPLACE_STATEMENT = "ReplaceStatement"
    CHANGE_VARIABLE = "ChangeVariable"
    ADD_ACTION = "AddAction"
    END_ACTION = "EndAction"
    JOIN_ACTION = "JoinAction"
    FUNDING = "Funding"
    PAYMENT = "Payment"
    PAY_BACK = "payBack"
    DIVIDEND = "Dividend"
    SET_MEMBERSHIP_HANDLER = "SetMembershipHandler"


class ProposalStatus(str, enum.Enum):
    DRAFT = "Draft"
    OUT_THERE = "OutThere"
    CANCELED = "Canceled"
    ON_THE_AIR = "OnTheAir"
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"


class PulseStatus(int, enum.Enum):
    NEXT = 0
    ACTIVE = 1
    DONE = 2


class StatementStatus(int, enum.Enum):
    ACTIVE = 1
    REMOVED = 2


# Maps proposal types to their threshold variable names
PROPOSAL_TYPE_THRESHOLDS: dict[ProposalType, str] = {
    ProposalType.MEMBERSHIP: "Membership",
    ProposalType.THROW_OUT: "ThrowOut",
    ProposalType.ADD_STATEMENT: "AddStatement",
    ProposalType.REMOVE_STATEMENT: "RemoveStatement",
    ProposalType.REPLACE_STATEMENT: "ReplaceStatement",
    ProposalType.CHANGE_VARIABLE: "ChangeVariable",
    ProposalType.ADD_ACTION: "AddAction",
    ProposalType.END_ACTION: "EndAction",
    ProposalType.JOIN_ACTION: "JoinAction",
    ProposalType.FUNDING: "Funding",
    ProposalType.PAYMENT: "Payment",
    ProposalType.PAY_BACK: "payBack",
    ProposalType.DIVIDEND: "Dividend",
    ProposalType.SET_MEMBERSHIP_HANDLER: "SetMembershipHandler",
}


DEFAULT_VARIABLES: dict[str, str] = {
    "PulseSupport": "50",
    "ProposalSupport": "25",
    "ChangeVariable": "50",
    "Membership": "50",
    "ThrowOut": "60",
    "AddStatement": "50",
    "RemoveStatement": "60",
    "AddAction": "50",
    "EndAction": "60",
    "ReplaceStatement": "60",
    "JoinAction": "50",
    "Funding": "50",
    "Payment": "50",
    "payBack": "50",
    "Dividend": "50",
    "SetMembershipHandler": "50",
    "MinCommittee": "2",
    "MaxAge": "2",
    "Name": "No Name",
    "seniorityWeight": "1",
    "membershipFee": "0",
    "dividendBySeniority": "0",
    "proposalCooldown": "0",
    "quorumThreshold": "0",
    "membershipHandler": "",
}
