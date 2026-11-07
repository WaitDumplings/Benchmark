#include "eval.h"
#include "operator.h"
#include <cassert>
#include <cmath>
#include <iostream>
double cal_tc(std::vector<int>&,int,int,double,double,Data&);
bool cal_score(std::vector<int>&,std::vector<std::tuple<int,int>>&,std::vector<double>&,std::vector<int>&,int&,int,Route&,double,double,Data&);
long call_count_move_eval=0;
double mean_duration_move_eval=0.0,mean_route_len=0.0;
clock_t find_best_time=0,find_bks_time=0;
int find_best_run=0,find_best_gen=0,find_bks_run=0,find_bks_gen=0;
bool find_better=false;
void near(double a,double b){if(std::abs(a-b)>1e-9){std::cerr<<a<<" != "<<b<<"\n";std::abort();}}
Data load(char *path){
 ArgumentParser parser;parser.addArgument("--problem",1,false);parser.addArgument("--benchmark_reference");
 char arg0[]="test",arg1[]="--problem",arg3[]="--benchmark_reference";
 char *argv[]={arg0,arg1,path,arg3};parser.parse(4,argv);return Data(parser);
}
int main(int argc,char **argv){
 assert(argc==3);Data d=load(argv[1]);
 near(d.time[0][2],99.);near(d.time[2][0],7.);assert(d.hyperarc[0][2].empty());
 near(d.energy[0][1],3.125);assert(d.all_time>0 && d.all_dist>0);
 std::vector<int> route{0,1,3,2,0};std::vector<status> states;int flag,negative;double cost;
 update_route_status(route,states,d,flag,cost,negative);assert(flag==1);near(cost,126.);
 near(states[2].arr_RD,4.625);near(states[2].dep_RD,10.);near(states[2].dep_time,22.75);near(states.back().dep_time,30.75);
 route[2]=4;update_route_status(route,states,d,flag,cost,negative);assert(flag==1);near(states[2].dep_time,33.5);near(cost,126.);
 d.node[2].end=35.;update_route_status(route,states,d,flag,cost,negative);assert(flag==1);
 d.node[2].end=34.;update_route_status(route,states,d,flag,cost,negative);assert(flag==3);d.node[2].end=10000.;
 d.node[1].start=20.;route[2]=3;update_route_status(route,states,d,flag,cost,negative);assert(flag==1);near(states[1].dep_time,20.);near(states[2].dep_time,31.75);d.node[1].start=10.;
 d.node[0].end=30.;update_route_status(route,states,d,flag,cost,negative);assert(flag==3);d.node[0].end=10000.;
 d.node[1].delivery=70.;update_route_status(route,states,d,flag,cost,negative);assert(flag==2);d.node[1].delivery=30.;
 auto e=d.energy[0][1];d.energy[0][1]=11.;update_route_status(route,states,d,flag,cost,negative);assert(flag==4 && negative==1);d.energy[0][1]=e;
 std::vector<int> empty{0,0};update_route_status(empty,states,d,flag,cost,negative);assert(flag==1);near(cost,0.);
 // Same customers split across routes changes the actual weighted fixed-cost objective.
 Route joined(d);joined.node_list=route;joined.update(d);near(joined.cal_cost(d),126.);
 Route a(d),b(d);a.node_list={0,1,0};b.node_list={0,2,0};a.update(d);b.update(d);
 near(a.cal_cost(d)+b.cal_cost(d)-joined.cal_cost(d),92.);
 // Delivery-only construction must not divide 0 by total pickup=0.
 std::vector<int> base{0,1,0};assert(std::isfinite(cal_tc(base,2,2,40.,0.,d)));
 // Directed reverse subsequence distance and O(1) move delta must match route reconstruction.
 Route rev(d);rev.node_list={0,1,2,0};rev.update(d);
 near(rev.gat(2,1).dist,d.dist[2][1]);
 // Subproblem carries raw energy and station-specific charging without Floyd closure.
 std::vector<int> subset{2,1};Data sub(d,subset);near(sub.time[0][1],99.);near(sub.energy[0][2],3.125);near(sub.charging_seconds_per_kwh[3],2.);
 Data l=load(argv[2]);Route long_route(l);long_route.node_list.clear();long_route.node_list.push_back(0);
 for(int i=1;i<=120;++i)long_route.node_list.push_back(i);long_route.node_list.push_back(0);long_route.update(l);
 update_route_status(long_route.node_list,long_route.status_list,l,flag,cost,negative);assert(flag==1);assert(long_route.status_list.size()==122);near(cost,342.);
 std::vector<int> partial{0};for(int i=1;i<120;++i)partial.push_back(i);partial.push_back(0);
 assert(std::isfinite(cal_tc(partial,120,80,.5,0.,l)));
 long_route.temp_node_list=partial;std::vector<int> positions,indexes(l.node_num);std::vector<double> scores(l.node_num);
 std::vector<std::tuple<int,int>> unrouted{std::make_tuple(120,-1)};int score_len=0;l.n_insert=RCRS;
 assert(cal_score(positions,unrouted,scores,indexes,score_len,1,long_route,.5,0.,l));assert(score_len==1);
 std::string opt="2opt*";l.get_mem(opt,119,120).len_1=3;l.clear_mem();assert(l.get_mem(opt,119,120).len_1==0);
 std::cout<<"PASS directed matrix preservation, raw energy, full charge, heterogeneous power, hard TW/wait/return, volume, empty route K, weighted K move, delivery-only, reverse arc, subproblem and >100-node route\n";
}
