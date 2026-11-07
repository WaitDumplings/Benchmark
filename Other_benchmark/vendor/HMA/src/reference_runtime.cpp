#include "solution.h"
#include "reference_runtime.h"
#include <cstdlib>
#include <thread>
#include <fstream>
#include <iomanip>
namespace {
bool enabled=false;
double started=0, budget=0, best=double(INFINITY);
long emitted=0;
std::string events_path, ack_path, stop_path;
double monotonic_now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
std::string env(const char *name) { const char *x=std::getenv(name); return x?x:""; }
}
void reference_initialize(bool requested) {
    enabled=requested;
    if (!enabled) return;
    started=env("HMA_START_MONOTONIC").empty()?monotonic_now():std::stod(env("HMA_START_MONOTONIC"));
    budget=std::stod(env("HMA_BUDGET_S"));
    if (!(budget>0)) throw std::runtime_error("HMA_BUDGET_S must be positive");
    events_path=env("HMA_EVENTS_PATH"); ack_path=env("HMA_ACK_PATH"); stop_path=env("HMA_STOP_PATH");
    if (events_path.empty()) throw std::runtime_error("HMA_EVENTS_PATH is required");
}
double reference_elapsed() { return enabled?monotonic_now()-started:0.0; }
void reference_check_deadline() { if (enabled && reference_elapsed()>=budget) throw ReferenceDeadline(); }
clock_t reference_clock() { return enabled?clock_t(reference_elapsed()*CLOCKS_PER_SEC):std::clock(); }
void reference_finish(const char *reason) {
    if (!enabled || stop_path.empty()) return;
    std::ofstream out(stop_path);
    out<<std::setprecision(17)<<"{\"stop_reason\":\""<<reason<<"\",\"elapsed_s\":"<<reference_elapsed()
       <<",\"budget_s\":"<<budget<<",\"candidate_count\":"<<emitted<<"}\n";
}
void reference_emit_candidate(Solution &s, Data &data, const char *stage, bool customer_lists) {
    if (!enabled || data.is_subproblem || !std::isfinite(s.cost) || !(s.cost<best-1e-9)) return;
    reference_check_deadline();
    // Native consistency gate only: external canonical verification determines final feasibility and cost.
    std::vector<std::vector<int>> routes;
    std::vector<int> counts(data.customer_num+1,0);
    double evaluated=0.0;
    for (int i=0; i<s.len(); ++i) {
        Route &r=s.get(i);
        std::vector<int> nl=customer_lists?r.customer_list:r.node_list;
        bool serves=false;
        for (int n:nl) if (n>=1 && n<=data.customer_num) { serves=true; ++counts[n]; }
        if (!serves) continue;
        std::vector<status> states; int flag=0,negative=-1; double cost=0;
        reference_route_status(nl,states,data,flag,cost,negative,false);
        if (flag!=1) return;
        evaluated+=cost; routes.push_back(nl);
    }
    for (int i=1;i<=data.customer_num;++i) if (counts[i]!=1) return;
    if (std::abs(evaluated-s.cost)>1e-5) return; // never publish a stale or penalty objective as a feasible incumbent
    reference_check_deadline();
    double elapsed=reference_elapsed();
    std::ofstream out(events_path,std::ios::app);
    if (!out) throw std::runtime_error("Cannot append HMA candidate journal");
    out<<std::setprecision(17)<<"{\"elapsed_s\":"<<elapsed<<",\"internal_objective\":"<<s.cost
       <<",\"stage\":\""<<stage<<"\",\"routes\":[";
    for (size_t i=0;i<routes.size();++i) { if(i)out<<",";out<<"[";
        for(size_t j=0;j<routes[i].size();++j){if(j)out<<",";out<<routes[i][j];}out<<"]"; }
    out<<"]}\n"; out.flush();
    if (!out) throw std::runtime_error("Cannot flush HMA candidate journal");
    best=s.cost; ++emitted;
    if (!ack_path.empty()) for (;;) {
        reference_check_deadline();
        std::ifstream ack(ack_path); long count=-1; ack>>count;
        if (count>=emitted) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
}
