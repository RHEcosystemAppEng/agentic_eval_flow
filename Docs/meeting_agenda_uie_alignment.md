```
Meeting Agenda
ABEvalFlow × UIE Skills Evaluation Alignment
──────────────────────────────────────────────

1. Skills Evaluation Pipeline — Updates & Status (5 min)
   - Current capabilities: skill/agent/MCP server evaluation, scorecard generation,
     certification levels (Foundational / Trusted / Certified)
   - Compass Facts integration: pushing gate results and certification as Soundcheck facts
   - Jira: APPENG-4901 (https://redhat.atlassian.net/browse/APPENG-4901)

2. Open Items — Guy's Side (20 min)

   a. Pipeline Deployment Ownership [DISCUSS]
      Who deploys and maintains the pipeline in the UIE tenant cluster?
      ABEvalFlow team can assist with setup — need a clear owner on the UIE side
      post-deployment.
      Jira: APPENG-5323 (https://redhat.atlassian.net/browse/APPENG-5323)

   b. Scorecard & Certification Definition Alignment [VALIDATE]
      Review concrete examples of the scorecard and certification structure.
      Validate current implementation matches Compass expectations.
      Scorecard structure reference:
        - Spreadsheet: https://docs.google.com/spreadsheets/d/1DyaGjY1Je-ElpVWlgi8Oq3OzAluliKly_KOSMs6pPI4/edit?pli=1&gid=463335999#gid=463335999
        - Doc: https://docs.google.com/document/d/1-inB77UJ_V8MAH2Tf_XK1PFaaRDHOyPcSBmBrT7LfNY/edit?tab=t.ee55xobfdyww
      Jira: APPENG-5307 (https://redhat.atlassian.net/browse/APPENG-5307)
            APPENG-5308 (https://redhat.atlassian.net/browse/APPENG-5308)

   c. Facts → Compass Integration Verification [CONFIRM]
      Confirm facts are pushed correctly and visible in the Compass UI.
      Requires a deployed environment — agree on validation approach.
      Jira: APPENG-5306 (https://redhat.atlassian.net/browse/APPENG-5306)
            APPENG-5491 (https://redhat.atlassian.net/browse/APPENG-5491)

   d. Skill Submission Git Workflow [CONFIRM]
      Confirm the production git repo for skill submissions and pipeline triggering.
      Currently using a test repo — need alignment on the final setup.
      Jira: APPENG-4993 (https://redhat.atlassian.net/browse/APPENG-4993)

3. Open Items — UIE/Firefly Side (Cassie) (5 min)
   - Project Firefly requirements: what does UIE need from a skill evaluation pipeline?
   - Where should evaluated skills live? Ownership of the skills artifact lifecycle.
   Jira: APPENG-5307 (https://redhat.atlassian.net/browse/APPENG-5307)
         APPENG-5308 (https://redhat.atlassian.net/browse/APPENG-5308)

4. Expected Outcomes
   - [ ] Identified owner for pipeline deployment on UIE side
   - [ ] Confirmed scorecard structure alignment (or identified gaps)
   - [ ] Agreed on validation approach for Facts → Compass
   - [ ] Confirmed production git repo for skill submissions
   - [ ] Clarified Firefly requirements and skill artifact ownership

──────────────────────────────────────────────
Total Time: ~30 min
```
