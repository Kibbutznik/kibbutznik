# KBZ (Kibutznik) System Logic

## Overview
The Kibutznik (KBZ) system is a pulse-based direct democracy governance platform that enables communities to make collective decisions through proposals and member support (not traditional voting). Pulses are governance cycles that advance based on member pulse support, not on time intervals.

---

## 1. Core Entities

### 1.1 Communities
Communities are the fundamental organizational unit in the system.

**Key Characteristics:**
- Each community has a unique `community_id` (UUID)
- Communities are hierarchical - each has a `parent_community_id` 
- Root communities have `parent_community_id = 00000000-0000-0000-0000-000000000000` (nil UUID)
- Communities have a `status` field (1 = active, 2 = inactive)
- Communities track member count via `member_counters` table

**Community Hierarchy:**
- Parent communities can have multiple child communities
- Child communities inherit from but operate independently of parents
- The system can traverse up (getParentsTree) or down (getChildrenTree) the hierarchy
- Relationship checks: `isParentOf()`, `isChildOf()`
- **Transparency**: All actions (sub-communities) are completely transparent to all parent community members 

### 1.2 Actions
Actions are special child communities that represent projects, initiatives, or working groups. They can also be seen as committees that execute specific tasks. 
**Key Characteristics:**
- An Action is implemented as a child community
- `action_id` equals `community_id`
- Actions have their own members, variables, and governance
- Actions can be ended (status set to 2) via proposal
- Actions are tracked in both `actions` and `actions_by_parent` tables

### 1.3 Users
Users are individuals who can participate in communities.

**Key Characteristics:**
- Each user has a unique `user_id` (UUID)
- User attributes: `user_name`, `password`, `about`, `wallet_address`
- Users can be members of multiple communities
- Users create proposals and provide support

### 1.4 Members
Members represent the relationship between users and communities. Each community installation is stand-alone. 
**Key Characteristics:**
- A member is defined by `community_id` + `user_id`
- Status: 1 = active member, 2 = thrown out
- Each member has a `seniority` value that increases with each pulse
- Members are tracked in multiple tables:
  - `members_by_community` - query members by community
  - `members_by_user` - query communities by user
  - `members_by_seniority` - query members by seniority level
- Community member count is maintained in `member_counters`

**Seniority:**
- Starts at 0 when member joins
- Increments by 1 with each community pulse
- Used to track member engagement and longevity
- Stored in `seniority_counters` table

---

## 2. Governance Variables

### 2.1 Default Variables
System-wide default values for governance thresholds.

**Base Default Variables:**
```
PulseSupport: 50%         - Support needed to execute a pulse
ProposalSupport: 25%      - Support needed to assign proposal to pulse
ChangeVariable: 50%       - Support needed to change a variable
Membership: 50%           - Support needed to grant membership
ThrowOut: 60%             - Support needed to revoke membership
AddStatement: 50%         - Support needed to accept new statement
RemoveStatement: 60%      - Support needed to cancel statement
AddAction: 50%            - Support needed to accept new action
EndAction: 60%            - Support needed to cancel action
ReplaceStatement: 60%     - Support needed to replace statement
JoinAction: 50%           - Support needed to assign member to action
Funding: 50%              - Support needed to fund an action
Payment: 50%              - Support needed to make a payment
payBack: 50%              - Support needed to decide on action payback
Dividend: 50%             - Support needed to give dividend to members
SetMembershipHandler: 50% - Support needed to designate action as membership handler
MinCommittee: 2           - Minimum size of action committee
MaxAge: 2                 - Maximum 'OutThere' proposal age (in pulses)
Name: "No Name"           - Community name
seniorityWeight: 1        - Formula for vote weight by seniority (1 = equal)
membershipFee: 0          - Crypto amount required to join (0 = free)
dividendBySeniority: 0    - Distribute dividends by seniority (0 = equal, 1 = weighted)
proposalCooldown: 0       - Minimum pulses between same-type proposals (0 = no cooldown)
quorumThreshold: 0        - Minimum participation % for valid pulse (0 = no requirement)
membershipHandler: null   - Action ID that handles membership (null = parent handles)
```

### 2.2 Community Variables
Each community gets its own copy of variables that can be modified through governance.

**Operations:**
- `initializeCommunityVariables()` - Copies defaults when community is created
- `getVariableValue()` - Gets a specific variable value
- `updateVariableValue()` - Updates a variable (requires proposal approval)
- `fetchCommunityVariables()` - Gets all variables as a map

**New Variables Added:**
1. **seniorityWeight** - Controls how member seniority affects support power. Default '1' means equal weight for all.
2. **membershipFee** - Amount required to join community (default 0 = free)
3. **dividendBySeniority** - Whether dividends are distributed based on seniority weight
4. **proposalCooldown** - Prevents spam by requiring minimum pulses between proposals
5. **quorumThreshold** - Ensures minimum participation for pulse validity
6. **membershipHandler** - Allows delegation of membership approval to specific actions 
---


## 3. Pulses (Governance Cycles)

### 3.1 Pulse Lifecycle
Pulses are **the heart of the governance system** - they are support-based (not time-based) governance cycles that advance the decision-making process. **The pulse is the only way to advance the system.** 

**Pulse Statuses:**
- `0 = Next` - The upcoming pulse, accepting pulse support votes
- `1 = Active` - Currently active pulse, processing proposals
- `2 = Done` - Completed pulse

**Pulse Flow:**
1. Community is created → Initial pulse created with status 0 (Next)
2. Members provide pulse support to the Next pulse !!execution!!!
3. When support threshold is reached → pulse executes
4. Next pulse (0) becomes Active (1)
5. New Next pulse (0) is created
6. Active pulse processes proposals
7. Active pulse becomes Done (2)

### 3.2 Pulse Support
Members provide support to trigger the next pulse cycle. **This is the only way to advance the system.**

**Key Operations:**
- `PulseSupport.create()` - Member adds support for next pulse
- `PulseSupport.delete()` - Member removes support
- Support is counted in `pulse_counters` table (or equivalent mechanism to track current pulse support in real-time)
- When `pulse_support >= threshold` → pulse executes automatically
- Threshold = `Math.ceil(member_count * PulseSupport_variable / 100)`

### 3.3 Pulse Execution
When a pulse executes, the community goes through a governance cycle:

**What Happens During Pulse:**
1. **OutThere → OnTheAir**: Proposals with enough support move to active pulse
   - Check each OutThere proposal's support count
   - If `support >= Math.ceil(member_count * proposal_type_threshold / 100)` → move to OnTheAir
   - Increment proposal age
   - If `age > MaxAge` → cancel proposal
2. **OnTheAir → Accepted/Rejected**: Active pulse proposals are decided
   - Calculate if each proposal has majority support
   - If `(support / member_count * 100) > variable_value` → accept (variable is defined by the proposal type threshold)
   - Otherwise → reject
   - Execute accepted proposals
3. **Member Seniority**: Increment seniority for all active members
4. **Update Counters**: Update member count

---

## 4. Proposals

### 4.1 Proposal Lifecycle
Proposals go through distinct stages from creation to execution.

**Proposal Statuses:**
- `Draft` - Created but not submitted for consideration
- `OutThere` - Submitted, gathering support
- `Canceled` - Canceled before reaching active pulse
- `OnTheAir` - In active pulse, being voted on
- `Accepted` - Approved and executed
- `Rejected` - Voted down

**Lifecycle Flow:**
```
Draft → OutThere → OnTheAir → Accepted
                           ↘ Rejected
          ↘ Canceled
```

### 4.2 Proposal Types
Different types of proposals for different governance actions.

**Proposal Types** (complete list of all possible proposal types):
1. **Membership** - Add new member to community
2. **ThrowOut** - Remove member from community
3. **AddStatement** - Add new statement to community
4. **RemoveStatement** - Remove existing statement
5. **ReplaceStatement** - Replace one statement with another
6. **ChangeVariable** - Change a governance variable value
7. **AddAction** - Create new action (child community)
8. **EndAction** - End an existing action
9. **JoinAction** - Add member to action committee
10. **Funding** - Allocate funds to an action
11. **Payment** - Make a payment from community
12. **payBack** - Decide on action payback distribution
13. **Dividend** - Distribute dividends to members
14. **SetMembershipHandler** - Designate an action to handle membership approval

### 4.3 Proposal Fields
**Core Fields:**
- `proposal_id` - Unique identifier
- `community_id` - Which community this proposal belongs to
- `user_id` - Who created the proposal
- `proposal_type` - Type from enum above
- `proposal_status` - Current status
- `proposal_text` - Text description/explanation
- `val_uuid` - Optional UUID value (e.g., user_id for Membership)
- `val_text` - Optional text value (e.g., new variable value)
- `pulse_id` - Which pulse the proposal is assigned to (null if OutThere)
- `age` - How many pulses the proposal has been OutThere

### 4.4 Proposal Support
Members provide support for proposals to move them forward.

**Support Mechanics:**
- Members call `Support.create(user_id, proposal_id, support_value)`
- Support value indicates agreement (implementation detail)
- Support count tracked in `proposal_counters` table (or any efficient counter mechanism)
- Support determines if proposal moves from OutThere → OnTheAir
- Different thresholds for OutThere→OnTheAir vs final acceptance

**Two-Stage Support:**
1. **OutThere Stage**: Need threshold % to get onto pulse
   - Example: ProposalSupport = 15% means 15% of members must support
2. **OnTheAir Stage**: Need threshold % for final acceptance
   - Example: Membership = 50% means 50% of members must support

### 4.5 Proposal Execution
When a proposal is accepted, it executes its specific action.

**Execution by Type:**
- `Membership` → `Members.create(community_id, user_id)`
- `ThrowOut` → `Members.throwOut(community_id, val_uuid)`
- `AddStatement` → `Statements.create(community_id, proposal_text)`
- `RemoveStatement` → `Statements.removeStatement(community_id, val_uuid)`
- `ReplaceStatement` → `Statements.replaceStatement(community_id, val_uuid, val_text)`
- `ChangeVariable` → `Variables.updateVariableValue(community_id, proposal_text, val_text)`
- `AddAction` → `Actions.create(community_id, val_text)`
- `EndAction` → `Actions.endAction(val_uuid)`
- `JoinAction` → `Members.create(val_uuid, user_id)` (adds to action community)

---

## 5. Statements

### 5.1 Statement Purpose
Statements are community principles, declarations, or rules.

**Key Characteristics:**
- Each statement has `statement_id`, `community_id`, `statement_text`
- Status: 1 = active, 2 = removed
- Statements can reference a previous statement via `prev_statement_id`
- Forms a version history chain

### 5.2 Statement Operations
- **Create**: Add new statement to community
- **Remove**: Mark statement as removed (status = 2)
- **Replace**: Remove old statement, create new one with reference to old
- **History**: Can traverse `prev_statement_id` chain to see evolution

**Statement as Constitution**: The statement acts as the constitution of the community. A member needs to sign/acknowledge it when proposing to join the community. 

---

## 6. Comments

### 6.1 Comment System
Comments enable discussion on various entities.

**Key Characteristics:**
- Can comment on any entity (proposal, community, etc.)
- `entity_id` + `entity_type` identifies what is being commented on
- Comments can be nested via `parent_comment_id`
- Each comment has a `score` that can be incremented/decremented
- Comments sorted by score in `comments_by_entity` table

### 6.2 Comment Operations
- `addComment()` - Add new comment or reply
- `getComments()` - Get all comments for an entity
- `incrementScore()` / `decrementScore()` - Support/vote on comments (HackerNews-style)
- `getReplies()` - Get replies to a specific comment

---

## 7. User Closeness

### 7.1 Closeness Calculation
Tracks relationship strength between users based on voting patterns.

**Mechanics:**
- Score stored for each user pair (always user_id1 < user_id2)
- Score calculated based on agreement/disagreement on proposals
- When two users support same proposal with same value: +10 points
- When two users support same proposal with different values: -10 points
- `last_calculation` timestamp tracks incremental updates

### 7.2 Closeness Operations
- `calc()` - Calculate/update closeness between two users
- `find()` - Get closeness record for user pair
- `getCloseUsers()` - Find all users close to a given user (above threshold)

---

## 8. Data Model Tables

### 8.1 Primary Tables
- `communities` - Community records
- `users` - User accounts
- `proposals` - Proposal records
- `pulses` - Pulse records
- `statements` - Statement records
- `actions` - Action records
- `members_by_community` - Member lookup by community
- `members_by_user` - Member lookup by user
- `variables` - Community variable values
- `default_variable_values` - System defaults
- `supports` - Proposal support by user
- `pulse_supports` - Pulse support by user
- `comments` - Comment records
- `closeness_records` - User closeness scores

### 8.2 Index Tables (for efficient queries)
- `communities_by_parent` - Child communities by parent
- `actions_by_parent` - Actions by parent community
- `members_by_seniority` - Members by seniority level
- `proposals_by_community` - Proposals by community
- `proposals_by_status` - Proposals by status and community
- `proposals_by_type` - Proposals by type and community
- `proposals_by_pulse` - Proposals by pulse
- `pulses_by_community_status` - Pulses by community and status
- `supports_by_proposal` - Supports by proposal
- `pulse_supports_by_user` - Pulse supports by user
- `comments_by_entity` - Comments by entity (sorted by score)

### 8.3 Counter Tables (for aggregations)
- `member_counters` - Total member count per community
- `seniority_counters` - Seniority value per member
- `proposal_counters` - Support count and age per proposal
- `pulse_counters` - Support count and threshold per pulse

---

## 9. Key Workflows

### 9.1 Create New Community
1. Generate new `community_id`
2. Insert into `communities` table with parent
3. Insert into `communities_by_parent` index
4. Initialize `member_counters` to 0
5. Copy all default variables to community
6. Set community Name variable
7. Add founding user as member
8. Create initial Next pulse (status 0)

### 9.2 User Joins Community

**Standard Membership Flow:**
1. User creates Membership proposal with their `user_id`
2. Membership proposals automatically start as OutThere
3. Other members provide support
4. When next pulse executes:
   - If support >= Membership threshold → move to OnTheAir
5. When active pulse executes:
   - If support > Membership % → accept and execute
   - Execute: Add user to members tables, initialize seniority to 0

**Membership-Handling Action Flow** (IMPLEMENTED):
Communities can delegate membership approval to a designated action to make the system more dynamic and responsive:

1. **Setup**: Parent community creates a SetMembershipHandler proposal to designate an action as the membership handler
2. **Routing**: When membershipHandler is set, all new Membership proposals are automatically routed to that action's community
3. **Voting**: The action's members (elected committee) vote on membership proposals
4. **Execution**: When accepted, the member is added to the **parent community**, not the handler action
5. **Constraints**:
   - Each community can have **only one** membership handler action
   - Only the **parent community** can throw out members (not the handler action)
   - ThrowOut proposals must be submitted to and approved by the parent community
   - This takes load off the parent community while maintaining parent authority over removal


### 9.3 Submit and Pass Proposal
1. User creates proposal (type + required values)
2. User or creator marks as OutThere (status change)
3. Members provide support via `Support.create()`
4. Proposal ages with each pulse
5. When next pulse executes:
   - If support >= proposal type threshold → assign to next pulse (OnTheAir)
   - If age > MaxAge → cancel
6. When active pulse executes:
   - If (support / member_count * 100) > threshold → accept and execute
   - Otherwise → reject
7. Execution performs the action (add member, change variable, etc.)

### 9.4 Trigger Pulse
1. Members call `PulseSupport.create()` on Next pulse
2. Counter increments in `pulse_counters`
3. When `pulse_support >= threshold`:
   - Call `Communities.pulse(community_id)`
4. Pulse execution:
   - Process Active pulse proposals (accept/reject/execute)
   - Move Next pulse to Active
   - Move OutThere proposals with enough support to new Active pulse
   - Age remaining OutThere proposals, cancel if too old
   - Increment all member seniority
   - Update member count
   - Create new Next pulse

### 9.5 Create Action (Sub-Community)
1. Member creates AddAction proposal with name in `val_text`
2. Proposal goes through normal workflow (OutThere → OnTheAir → Accepted)
3. On execution:
   - Create new community as child of current community
   - Create action record linking `action_id` to `community_id`
   - Initialize action with default variables
   - Action operates as independent community with own governance
4. Members can join action via JoinAction proposals
5. Action can be ended via EndAction proposal

---

## 10. Important Logic Rules

### 10.1 Threshold Calculations
- All percentage thresholds use: `Math.ceil(member_count * percentage / 100)`
- This ensures minimum whole number of required supporters
- Example: 15% of 10 members = Math.ceil(10 * 15 / 100) = 2 supporters

### 10.2 Proposal Age Limits
- Proposals increment `age` each time a pulse executes while they're OutThere
- If `age > MaxAge` variable → proposal is automatically canceled
- Default MaxAge = 2 pulses
- Prevents stale proposals from cluttering the system

### 10.3 One Active Pulse Per Community
- Each community has exactly one pulse with status 1 (Active)
- Each community has exactly one pulse with status 0 (Next)
- These are enforced during pulse creation and status updates
- Previous pulses remain with status 2 (Done) for history

### 10.4 Two-Stage Approval
Proposals need support at two different stages:
1. **OutThere → OnTheAir**: Lower threshold (typically 15% ProposalSupport)
   - Gets proposal onto the pulse agenda
2. **OnTheAir → Accepted**: Type-specific threshold (typically 50%+)
   - Actually executes the proposal

This prevents spam while ensuring only widely-supported proposals execute.

### 10.5 Member Seniority
- Seniority increases by 1 with each community pulse
- Used as a measure of member engagement and longevity
- Can be used to filter members (seniorityGTE, seniorityLTE)
- Maintained in `seniority_counters` for efficient queries

### 10.6 Support vs Opposition
- Current implementation tracks support count
- For agreement decisions: compare support_count to threshold
- Formula: `(support_count / member_count * 100) > threshold`
- No explicit opposition tracking - lack of support = opposition

---

## Clarifications and Design Decisions

### 1. Vote/Support Counting
**Implementation**: Added `seniorityWeight` variable that controls how member seniority affects support power.
- Default value: 1 (all members have equal support power)
- Future implementation will weight support based on seniority using this multiplier
- Formula: `support_weight = 1 + (seniority * seniorityWeight)`

### 2. Active Pulse Lifecycle
**Clarified**: When the active pulse ends:
- All proposals in it are executed if accepted
- Rejected proposals are marked as rejected (no execution)
- The next pulse is created and becomes active

### 3. Proposal Editing
**Rule**: Proposals can only be edited while in Draft state
- Once moved to OutThere, proposals are immutable
- This prevents vote manipulation after members have already provided support

### 4. Duplicate Support Prevention
**Implementation**: Database constraints prevent duplicate support
- A user must unsupport a proposal before supporting it again
- Support table has unique constraint on (user_id, proposal_id)

### 5. Financial Proposals Execution

**Payment Proposal**:
- Community pays from its wallet to a crypto address
- Address is public in the proposal so all members can verify legitimacy
- If accepted, community/action wallet sends the proposed amount to that address

**Dividend Proposal**:
- Pays members of the base community (not actions) their share of the proposed amount
- Added `dividendBySeniority` variable (default: 0)
  - 0 = equal distribution to all members
  - 1 = weighted by seniority

**Funding Proposal**:
- Complex, not yet implemented
- Alternative: Initial funding via membership fees
- Added `membershipFee` variable (default: 0 = free to join)

### 6. Comment Scoring
**Design**: HackerNews-style comment system
- Each comment has a score
- Members can increment/decrement scores
- Comments sorted by score (highest first)

### 7. Duplicate Membership Prevention
**Mechanism**: Membership fees
- Higher fees make it costly to create duplicate accounts
- Set via `membershipFee` variable

### 8. RestrictPayments Variable
**Decision**: Removed from system
- Unclear use case and functionality

### 9. Action Termination
**Rules**:
- Action ends when EndAction proposal is accepted within the action
- If action members act against parent community, parent can throw them out
- Parent community has ultimate authority over member removal

### 10. Variable Management
**Rule**: Variables can only be modified, not added/removed
- The variable list is constant and defined in DefaultVariables
- Values can be changed via ChangeVariable proposals

---

## New Features Summary

### Membership-Handling Actions
- Actions can be designated to handle membership approval for parent community
- Use SetMembershipHandler proposal type
- Membership proposals route to handler action
- Handler votes, but members are added to parent community
- Parent retains ThrowOut authority

### Seniority-Weighted Support
- Variable: `seniorityWeight` (default: 1 = equal)
- Future: Support weight = 1 + (seniority * seniorityWeight)
- Affects proposal and pulse support counting

### Membership Fees
- Variable: `membershipFee` (default: 0 = free)
- Payment required to join community
- Prevents duplicate memberships when set high

### Seniority-Based Dividends
- Variable: `dividendBySeniority` (default: 0 = equal)
- 0: Equal distribution to all members
- 1: Weighted by seniority

### Additional Governance Controls
- `proposalCooldown`: Minimum pulses between same-type proposals (spam prevention)
- `quorumThreshold`: Minimum participation % for valid pulse
