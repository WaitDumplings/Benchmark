#pragma once
#include <chrono>
#include <stdexcept>
#include <ctime>
class Data;
class Solution;
struct ReferenceDeadline : public std::runtime_error {
    ReferenceDeadline() : std::runtime_error("reference_wall_budget_exhausted") {}
};
void reference_initialize(bool enabled);
void reference_check_deadline();
double reference_elapsed();
clock_t reference_clock();
void reference_emit_candidate(Solution &s, Data &data, const char *stage, bool customer_lists=false);
void reference_finish(const char *reason);
