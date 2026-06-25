from typing import Literal, Optional
from pydantic import BaseModel, Field

class Job(BaseModel):
    
    job_url: str = Field(description="URL of the job posting")
    
class JobDetails(BaseModel):
    
    job_title: str = Field(description="Title of the job posting")
    description: str = Field(description="Description of the job posting")
    salary: Optional[int] = Field(description="Salary of the job posting")
    
class JobList(BaseModel):
    
    jobs: list[Job] = Field(description="Jobs obtained from the page scrape")
    
class AppliableJob(BaseModel):

    reasoning: str = Field(description="Brief explanation of the recommendation covering experience match, tech stack overlap, seniority fit, degree requirement, and transferable relevance.")
    recommendation: Literal['APPLY', 'STRETCH', 'SKIP'] = Field(description="""
        - APPLY: Strong overlap, junior-friendly, realistic requirements
        - STRETCH: Some gaps but worth trying (e.g. bachelor's preferred not required, or 2-3 yr exp req)
        - SKIP: Major mismatches (senior-level, wrong stack, 5+ years required, requires degree candidate doesn't have)
                                                                """)
    # user_should_apply_to_job: bool = Field(description="Boolean to determine whether or not the user should apply to this job.")
    # job_url: str = Field(description="URL of the webpage the job is hosted on")
    # apply_reason: str = Field(description="Reason as to why the job selected should be applied to.")
    
class Prettyizer(BaseModel):
    
    formatted_content: str = Field(description="Easy to read markdown conversion of input contents. The markdown file should also contain the links to the job postings.")