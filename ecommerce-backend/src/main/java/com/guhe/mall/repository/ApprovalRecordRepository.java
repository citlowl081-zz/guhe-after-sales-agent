package com.guhe.mall.repository;

import com.guhe.mall.entity.ApprovalRecord;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ApprovalRecordRepository extends JpaRepository<ApprovalRecord, Long> {

    List<ApprovalRecord> findByTargetNoOrderByCreatedAtDesc(String targetNo);
}
