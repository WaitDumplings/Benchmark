// Necessary benchmark task adaptation for RoutingBlocks revision 2d2ee06.
// Full recharge, heterogeneous station power, directed matrices, weighted cost.
#pragma once
#include <routingblocks/FRVCP.h>
#include <routingblocks/evaluation.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <limits>
#include <optional>
#include <stdexcept>
#include <vector>

namespace routingblocks {
inline double fullcharge_monotonic_s() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
inline void fullcharge_check_deadline(double deadline) {
    if (fullcharge_monotonic_s() >= deadline) throw std::runtime_error("routingblocks_online_budget_expired");
}
struct FullRechargeVertexData {
    double demand, ready, due, service, power_kw;
    FullRechargeVertexData(double d, double r, double l, double s, double p)
        : demand(d), ready(r), due(l), service(s), power_kw(p) {}
};
struct FullRechargeArcData {
    double distance, energy, duration;
    FullRechargeArcData(double d, double e, double t): distance(d), energy(e), duration(t) {}
};
struct FullRechargeLabel {
    double departure=0, battery=0, distance=0, load=0, energy_violation=0, lateness=0;
    double max_energy_violation=0, max_lateness=0;
    double charging_time=0;
    size_t customers=0, charging_stops=0;
};

// Prefix caching remains native; changed suffixes are replayed exactly. The
// partial-recharge constant-time concatenation identities do not apply here.
class FullRechargeEvaluation : public ForwardBasedEvaluation {
    double _battery, _capacity, _fixed, _alpha;
    mutable size_t _deadline_calls=0;
    void check_deadline() const { if ((++_deadline_calls & 1023)==0) fullcharge_check_deadline(deadline_s); }
public:
    double overload_penalty_factor=1, resource_penalty_factor=1, time_shift_penalty_factor=1;
    double deadline_s=std::numeric_limits<double>::infinity();
    FullRechargeEvaluation(double battery, double capacity, double fixed, double alpha)
      : _battery(battery), _capacity(capacity), _fixed(fixed), _alpha(alpha) {}
    FullRechargeLabel advance(const FullRechargeLabel& pred, const Vertex& vertex, const Arc& arc) const {
        check_deadline();
        auto x=pred;
        const auto& v=vertex.get_data<FullRechargeVertexData>();
        const auto& a=arc.get_data<FullRechargeArcData>();
        x.distance+=a.distance;
        double remaining=x.battery-a.energy;
        x.energy_violation+=std::max(0., -remaining);
        x.max_energy_violation=std::max(x.max_energy_violation,-remaining);
        // The canonical replay keeps a negative battery (also within its
        // numerical tolerance) until full recharge; do not silently clamp it.
        x.battery=remaining;
        double start=x.departure+a.duration;
        if (!vertex.is_station) {
            start=std::max(start, v.ready);
            x.lateness+=std::max(0., start-v.due);
            x.max_lateness=std::max(x.max_lateness,start-v.due);
        }
        x.departure=start+v.service;
        if (vertex.is_station) {
            if (!(v.power_kw>0)) throw std::runtime_error("nonpositive effective station power");
            double dt=std::max(0.,_battery-x.battery)*3600./v.power_kw;
            x.departure+=dt;
            x.charging_time+=dt;
            ++x.charging_stops;
            x.battery=_battery;
        } else if (!vertex.is_depot) {
            ++x.customers;
            x.load+=v.demand;
        }
        return x;
    }
    double objective(const FullRechargeLabel& x) const { return x.customers ? _fixed+_alpha*x.distance : 0.; }
    cost_t compute_cost(const label_holder_t& label) const override {
        check_deadline();
        const auto& x=label.get<FullRechargeLabel>();
        if (is_feasible(label)) return objective(x);
        return objective(x)+overload_penalty_factor*std::max(0.,x.load-_capacity)
            +resource_penalty_factor*x.energy_violation+time_shift_penalty_factor*x.lateness;
    }
    bool is_feasible(const label_holder_t& label) const override {
        const auto& x=label.get<FullRechargeLabel>();
        return x.max_energy_violation<=1e-5 && x.max_lateness<=1e-5 && x.load<=_capacity+1e-5;
    }
    std::vector<resource_t> get_cost_components(const label_holder_t& label) const override {
        const auto& x=label.get<FullRechargeLabel>();
        return {objective(x), std::max(0.,x.load-_capacity), x.energy_violation, x.lateness};
    }
    label_holder_t propagate_forward(const label_holder_t& pred, const Vertex&, const Vertex& v, const Arc& a) const override {
        return label_holder_t(std::make_shared<FullRechargeLabel>(advance(pred.get<FullRechargeLabel>(),v,a)));
    }
    label_holder_t propagate_backward(const label_holder_t& succ,const Vertex&,const Vertex&,const Arc&) const override {
        // ForwardBasedEvaluation never reads a backward label.
        return succ;
    }
    label_holder_t create_forward_label(const Vertex& v) override {
        FullRechargeLabel x;
        x.departure=v.get_data<FullRechargeVertexData>().ready;
        x.battery=_battery;
        return label_holder_t(std::make_shared<FullRechargeLabel>(x));
    }
    label_holder_t create_backward_label(const Vertex& v) override { return create_forward_label(v); }
    std::vector<double> replay(const Instance& instance,const std::vector<VertexID>& route) {
        if (route.size()<2 || route.front()!=0 || route.back()!=0) throw std::runtime_error("route must start/end at depot");
        auto h=create_forward_label(instance.Depot());
        auto x=h.get<FullRechargeLabel>();
        for(size_t i=1;i<route.size();++i) x=advance(x,instance.getVertex(route[i]),instance.getArc(route[i-1],route[i]));
        return {objective(x),x.distance,x.load,x.energy_violation,x.lateness,x.departure,x.battery,x.charging_time,
            double(x.charging_stops),double(x.customers)};
    }
};

struct FullRechargeDPLabel {
    const FullRechargeDPLabel* predecessor=nullptr;
    VertexID vertex_id=0;
    double cost=0, departure=0, battery=0;
    size_t stations=0;
    std::vector<bool> visited;
};
template<> class Propagator<FullRechargeDPLabel> {
    const Instance* _instance;
    double _battery,_alpha,_deadline;
public:
    Propagator(const Instance& instance,double battery,double alpha,double deadline)
      :_instance(&instance),_battery(battery),_alpha(alpha),_deadline(deadline) {}
    std::optional<FullRechargeDPLabel> propagate(const FullRechargeDPLabel& pred,const Vertex& origin,const Vertex& target,const Arc& arc) {
        fullcharge_check_deadline(_deadline);
        if(target.is_station && pred.visited[target.id]) return {};
        const auto& a=arc.get_data<FullRechargeArcData>();
        const auto& v=target.get_data<FullRechargeVertexData>();
        auto x=pred;
        x.predecessor=&pred;
        x.vertex_id=target.id;
        x.cost+=_alpha*a.distance;
        x.battery-=a.energy;
        if(x.battery < -1e-5) return {};
        x.departure+=a.duration;
        if(target.is_station) {
            x.departure+=(_battery-x.battery)*3600./v.power_kw;
            x.battery=_battery;
            x.visited[target.id]=true;
            ++x.stations;
        } else {
            x.departure=std::max(x.departure,v.ready);
            if(x.departure>v.due+1e-5) return {};
            x.departure+=v.service;
            std::fill(x.visited.begin(),x.visited.end(),false);
        }
        return x;
    }
    bool dominates(const FullRechargeDPLabel& a,const FullRechargeDPLabel& b) {
        if(a.cost>b.cost || a.departure>b.departure || a.battery<b.battery) return false;
        // A dominance label must not forbid a station available to the other.
        for(size_t i=0;i<a.visited.size();++i) if(a.visited[i]&&!b.visited[i]) return false;
        return true;
    }
    bool cheaper_than(const FullRechargeDPLabel& a,const FullRechargeDPLabel& b) {
        return a.cost==b.cost ? a.stations<b.stations : a.cost<b.cost;
    }
    bool should_order_before(const FullRechargeDPLabel& a,const FullRechargeDPLabel& b) { return a.departure<b.departure; }
    bool is_final_label(const FullRechargeDPLabel& x) { return x.vertex_id==0 && x.predecessor!=nullptr; }
    std::vector<VertexID> extract_path(const FullRechargeDPLabel& last) {
        std::vector<VertexID> path;
        for(auto p=&last;p;p=p->predecessor) path.push_back(p->vertex_id);
        std::reverse(path.begin(),path.end());return path;
    }
    void prepare(const std::vector<VertexID>&) { fullcharge_check_deadline(_deadline); }
    FullRechargeDPLabel create_root_label() {
        FullRechargeDPLabel x;
        x.battery=_battery;
        x.departure=_instance->Depot().get_data<FullRechargeVertexData>().ready;
        x.visited.resize(_instance->NumberOfVertices(),false);
        return x;
    }
};
}
